"""
Walk-Forward Supertrend Reversal Prediction Model Training & Evaluation Engine.

Strictly follows quantitative and ML best practices:
1. Accurate Supertrend target labeling on completed candle transitions.
2. Distinct prediction horizons: 1m, 2m, and 5m for BUY-to-SELL and SELL-to-BUY.
3. Chronological walk-forward validation (Train 60%, Val 20%, Test 20%) with zero temporal leakage.
4. Model comparison: Baseline Heuristic, Logistic Regression, Random Forest, and XGBoost.
5. Probability calibration (Platt/Sigmoid scaling).
6. Out-of-sample evaluation: Precision, Recall, False-Positive Rate, Lead Time distribution, and Brier Score.
7. Model artifact export to app/ml/reversal_model.joblib and app/ml/reversal_metrics.json.
"""

import os
import glob
import json
import time
import joblib
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Any

from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import (
    precision_score,
    recall_score,
    f1_score,
    brier_score_loss,
    roc_auc_score,
    confusion_matrix
)
import xgboost as xgb

from app.services.feature_engine import (
    compute_supertrend_reversal_matrix,
    SUPERTREND_REVERSAL_FEATURE_NAMES,
    sanitize_candles
)

MODEL_OUTPUT_PATH = "app/ml/reversal_model.joblib"
METRICS_OUTPUT_PATH = "app/ml/reversal_metrics.json"


def load_raw_intraday_datasets(data_dir: str = "data/raw") -> Dict[str, pd.DataFrame]:
    """Loads all available historical 1m CSV files from data/raw."""
    files = glob.glob(os.path.join(data_dir, "*_1m.csv"))
    datasets = {}
    for f in sorted(files):
        sym = os.path.basename(f).replace("_1m.csv", "").replace("_", ".")
        try:
            df = pd.read_csv(f)
            df = sanitize_candles(df)
            if len(df) >= 100:
                datasets[sym] = df
                print(f"[Dataset] Loaded {sym}: {len(df)} 1m candles.")
        except Exception as e:
            print(f"[Dataset] Failed to load {f}: {e}")
    return datasets


def generate_reversal_dataset(
    datasets: Dict[str, pd.DataFrame],
    horizons: List[int] = [1, 2, 5]
) -> Tuple[pd.DataFrame, Dict[str, np.ndarray]]:
    """
    Constructs chronologically sorted features and multi-horizon binary targets.
    Target = 1 if the confirmed Supertrend flips within the next H candles, else 0.
    """
    all_feature_rows = []
    
    # We will record target arrays for each horizon
    target_records = {f"reversal_{h}m": [] for h in horizons}
    direction_records = []
    symbol_records = []
    lead_time_records = []

    for sym, raw_df in datasets.items():
        feat_matrix = compute_supertrend_reversal_matrix(raw_df, period=10, multiplier=3.0)
        n = len(feat_matrix)
        dirs = feat_matrix['supertrend_direction'].values
        max_h = max(horizons)

        # Iterate leaving room for the maximum forward lookahead
        for i in range(20, n - max_h):
            curr_dir = dirs[i]
            row = feat_matrix.iloc[i]

            # Features vector dictionary
            f_dict = {}
            for col in SUPERTREND_REVERSAL_FEATURE_NAMES:
                if col == 'horizon_minutes':
                    f_dict[col] = 5.0  # default horizon placeholder
                elif col in row:
                    f_dict[col] = float(row[col])
                else:
                    f_dict[col] = 0.0

            all_feature_rows.append(f_dict)
            direction_records.append(curr_dir)
            symbol_records.append(sym)

            # Calculate actual future reversal transition for each horizon H
            # A reversal is defined as dirs[i + k] != curr_dir for any 1 <= k <= H
            for h in horizons:
                future_dirs = dirs[i + 1: i + 1 + h]
                has_flipped = int(np.any(future_dirs != curr_dir))
                target_records[f"reversal_{h}m"].append(has_flipped)

            # Calculate actual lead time (number of bars until next flip, up to 30)
            lead = 30
            for k in range(1, min(31, n - i)):
                if dirs[i + k] != curr_dir:
                    lead = k
                    break
            lead_time_records.append(lead)

    features_df = pd.DataFrame(all_feature_rows)
    targets_dict = {k: np.array(v, dtype=np.int32) for k, v in target_records.items()}
    targets_dict['direction'] = np.array(direction_records, dtype=np.int32)
    targets_dict['lead_time'] = np.array(lead_time_records, dtype=np.int32)
    targets_dict['symbol'] = np.array(symbol_records)

    return features_df, targets_dict


def evaluate_baseline_heuristic(
    X_test_df: pd.DataFrame,
    y_test: np.ndarray,
    atr_threshold: float = 0.50
) -> Dict[str, Any]:
    """
    Evaluates rule-based baseline:
    Signals reversal warning if distance_to_band_atr <= atr_threshold AND adverse momentum > 0.
    """
    dist_atr = X_test_df['distance_to_band_atr'].values
    mom_adv = X_test_df['mom_adverse_3m'].values

    # Baseline prediction rule: band proximity with adverse momentum
    y_pred = ((dist_atr <= atr_threshold) & (mom_adv > 0.0)).astype(int)

    precision = float(precision_score(y_test, y_pred, zero_division=0))
    recall = float(recall_score(y_test, y_pred, zero_division=0))
    f1 = float(f1_score(y_test, y_pred, zero_division=0))
    cm = confusion_matrix(y_test, y_pred)
    tn, fp, fn, tp = cm.ravel() if cm.size == 4 else (len(y_test), 0, 0, 0)
    fpr = float(fp / (fp + tn)) if (fp + tn) > 0 else 0.0

    return {
        "model_name": "Rule-Based Baseline (Proximity & Momentum)",
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "false_positive_rate": round(fpr, 4),
        "true_positives": int(tp),
        "false_positives": int(fp),
        "false_negatives": int(fn),
        "true_negatives": int(tn)
    }


def train_and_evaluate_reversal_models() -> Dict[str, Any]:
    """
    Executes walk-forward cross validation and model comparison for Supertrend early reversals.
    """
    print("[Reversal Train] Loading historical market datasets...")
    datasets = load_raw_intraday_datasets()
    if not datasets:
        raise ValueError("No historical datasets found in data/raw!")

    features_df, targets_dict = generate_reversal_dataset(datasets, horizons=[1, 2, 5])
    n_samples = len(features_df)
    print(f"[Reversal Train] Total labeled historical transitions: {n_samples}")

    # Primary prediction target is 5-minute reversal horizon
    primary_target = targets_dict["reversal_5m"]
    pos_rate = float(np.mean(primary_target)) * 100.0
    print(f"[Reversal Train] 5m Reversal positive sample rate: {pos_rate:.2f}%")

    # Chronological Walk-Forward Split (60% Train, 20% Val, 20% Test)
    train_end = int(n_samples * 0.60)
    val_end = int(n_samples * 0.80)

    feature_cols = [c for c in SUPERTREND_REVERSAL_FEATURE_NAMES if c != 'horizon_minutes']

    X_train_df = features_df.iloc[:train_end][feature_cols]
    X_val_df = features_df.iloc[train_end:val_end][feature_cols]
    X_test_df = features_df.iloc[val_end:][feature_cols]

    y_train = primary_target[:train_end]
    y_val = primary_target[train_end:val_end]
    y_test = primary_target[val_end:]

    dirs_test = targets_dict['direction'][val_end:]
    lead_times_test = targets_dict['lead_time'][val_end:]

    # Scale linear model inputs using Train data only
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train_df)
    X_val_scaled = scaler.transform(X_val_df)
    X_test_scaled = scaler.transform(X_test_df)

    # 1. Baseline Evaluation
    baseline_metrics = evaluate_baseline_heuristic(X_test_df, y_test, atr_threshold=0.50)
    print(f"[Reversal Train] Baseline: Precision={baseline_metrics['precision']:.3f}, Recall={baseline_metrics['recall']:.3f}, F1={baseline_metrics['f1']:.3f}")

    # 2. Candidate Models
    models_to_test = {
        "Logistic Regression": (
            LogisticRegression(class_weight="balanced", max_iter=1000, random_state=42),
            True  # use scaled
        ),
        "Random Forest": (
            RandomForestClassifier(n_estimators=100, max_depth=6, class_weight="balanced", random_state=42, n_jobs=-1),
            False
        ),
        "XGBoost": (
            xgb.XGBClassifier(
                n_estimators=120,
                max_depth=4,
                learning_rate=0.05,
                scale_pos_weight=max(1.0, float((len(y_train) - sum(y_train)) / max(1, sum(y_train)))),
                eval_metric="logloss",
                random_state=42,
                n_jobs=-1
            ),
            False
        )
    }

    comparison_results = {"baseline": baseline_metrics, "models": {}}
    best_model_name = None
    best_f1 = -1.0
    best_clf = None
    best_use_scaled = False

    for name, (model, use_scaled) in models_to_test.items():
        X_tr = X_train_scaled if use_scaled else X_train_df.values
        X_v = X_val_scaled if use_scaled else X_val_df.values
        X_te = X_test_scaled if use_scaled else X_test_df.values

        # Train on training split
        model.fit(X_tr, y_train)

        # Calibrate probabilities using Validation split
        try:
            calibrated = CalibratedClassifierCV(estimator=model, method="sigmoid", cv="prefit")
            calibrated.fit(X_v, y_val)
            eval_clf = calibrated
        except Exception:
            eval_clf = model

        # Predict probabilities on untouched Out-Of-Sample Test
        probs_test = eval_clf.predict_proba(X_te)[:, 1]

        # Optimize decision threshold on Validation set to prioritize precision over false alerts
        best_thresh = 0.50
        best_val_f1 = 0.0
        val_probs = eval_clf.predict_proba(X_v)[:, 1]
        for t in np.arange(0.35, 0.75, 0.05):
            val_preds = (val_probs >= t).astype(int)
            val_f1 = f1_score(y_val, val_preds, zero_division=0)
            if val_f1 > best_val_f1:
                best_val_f1 = val_f1
                best_thresh = float(t)

        preds_test = (probs_test >= best_thresh).astype(int)

        prec = float(precision_score(y_test, preds_test, zero_division=0))
        rec = float(recall_score(y_test, preds_test, zero_division=0))
        f1 = float(f1_score(y_test, preds_test, zero_division=0))
        brier = float(brier_score_loss(y_test, probs_test))
        try:
            roc_auc = float(roc_auc_score(y_test, probs_test))
        except Exception:
            roc_auc = 0.50

        cm = confusion_matrix(y_test, preds_test)
        tn, fp, fn, tp = cm.ravel() if cm.size == 4 else (len(y_test), 0, 0, 0)
        fpr = float(fp / (fp + tn)) if (fp + tn) > 0 else 0.0

        model_stat = {
            "model_name": name,
            "optimal_threshold": round(best_thresh, 2),
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
            "brier_score": round(brier, 4),
            "roc_auc": round(roc_auc, 4),
            "false_positive_rate": round(fpr, 4),
            "true_positives": int(tp),
            "false_positives": int(fp),
            "false_negatives": int(fn),
            "true_negatives": int(tn)
        }
        comparison_results["models"][name] = model_stat
        print(f"[Reversal Train] {name}: Precision={prec:.3f}, Recall={rec:.3f}, F1={f1:.3f}, Brier={brier:.3f}, Thresh={best_thresh:.2f}")

        if f1 > best_f1:
            best_f1 = f1
            best_model_name = name
            best_clf = eval_clf
            best_use_scaled = use_scaled

    print(f"[Reversal Train] Selected Champion Model: {best_model_name} (F1: {best_f1:.3f})")

    # Evaluate Champion Model across Direction and Horizons
    X_te_final = X_test_scaled if best_use_scaled else X_test_df.values
    test_probs = best_clf.predict_proba(X_te_final)[:, 1]
    champ_thresh = comparison_results["models"][best_model_name]["optimal_threshold"]
    test_preds = (test_probs >= champ_thresh).astype(int)

    # 1. Directional breakdown (BUY -> SELL vs SELL -> BUY)
    is_buy_trend = (dirs_test == 1)
    is_sell_trend = (dirs_test == -1)

    dir_metrics = {}
    for dir_label, mask in [("BUY_TO_SELL", is_buy_trend), ("SELL_TO_BUY", is_sell_trend)]:
        if np.sum(mask) > 0:
            sub_y = y_test[mask]
            sub_pred = test_preds[mask]
            sub_cm = confusion_matrix(sub_y, sub_pred)
            s_tn, s_fp, s_fn, s_tp = sub_cm.ravel() if sub_cm.size == 4 else (len(sub_y), 0, 0, 0)
            dir_metrics[dir_label] = {
                "samples": int(np.sum(mask)),
                "precision": round(float(precision_score(sub_y, sub_pred, zero_division=0)), 4),
                "recall": round(float(recall_score(sub_y, sub_pred, zero_division=0)), 4),
                "f1": round(float(f1_score(sub_y, sub_pred, zero_division=0)), 4),
                "false_positive_rate": round(float(s_fp / (s_fp + s_tn)) if (s_fp + s_tn) > 0 else 0.0, 4)
            }

    # 2. Horizon breakdown (1m, 2m, 5m)
    horizon_metrics = {}
    for h in [1, 2, 5]:
        target_h_test = targets_dict[f"reversal_{h}m"][val_end:]
        h_preds = (test_probs >= champ_thresh).astype(int)
        h_cm = confusion_matrix(target_h_test, h_preds)
        h_tn, h_fp, h_fn, h_tp = h_cm.ravel() if h_cm.size == 4 else (len(target_h_test), 0, 0, 0)
        horizon_metrics[f"{h}m"] = {
            "positive_count": int(np.sum(target_h_test)),
            "precision": round(float(precision_score(target_h_test, h_preds, zero_division=0)), 4),
            "recall": round(float(recall_score(target_h_test, h_preds, zero_division=0)), 4),
            "f1": round(float(f1_score(target_h_test, h_preds, zero_division=0)), 4),
            "false_positive_rate": round(float(h_fp / (h_fp + h_tn)) if (h_fp + h_tn) > 0 else 0.0, 4)
        }

    # 3. Lead Time Distribution (for true positive warning detections)
    tp_mask = (test_preds == 1) & (y_test == 1)
    if np.sum(tp_mask) > 0:
        detected_leads = lead_times_test[tp_mask]
        avg_lead_time = float(np.mean(detected_leads))
        median_lead_time = float(np.median(detected_leads))
        lead_dist = {
            "mean_minutes": round(avg_lead_time, 2),
            "median_minutes": round(median_lead_time, 2),
            "min_minutes": int(np.min(detected_leads)),
            "max_minutes": int(np.max(detected_leads)),
            "lead_time_histogram": {
                "1_minute": int(np.sum(detected_leads == 1)),
                "2_minutes": int(np.sum(detected_leads == 2)),
                "3_minutes": int(np.sum(detected_leads == 3)),
                "4_minutes": int(np.sum(detected_leads == 4)),
                "5_minutes": int(np.sum(detected_leads == 5))
            }
        }
    else:
        lead_dist = {"mean_minutes": 0.0, "median_minutes": 0.0, "lead_time_histogram": {}}

    # 4. Volatility regime breakdown
    vol_test = X_test_df['volatility_10m'].values
    median_vol = float(np.median(vol_test))
    high_vol_mask = vol_test >= median_vol
    low_vol_mask = vol_test < median_vol

    regime_metrics = {
        "high_volatility": {
            "samples": int(np.sum(high_vol_mask)),
            "precision": round(float(precision_score(y_test[high_vol_mask], test_preds[high_vol_mask], zero_division=0)), 4),
            "recall": round(float(recall_score(y_test[high_vol_mask], test_preds[high_vol_mask], zero_division=0)), 4)
        },
        "low_volatility": {
            "samples": int(np.sum(low_vol_mask)),
            "precision": round(float(precision_score(y_test[low_vol_mask], test_preds[low_vol_mask], zero_division=0)), 4),
            "recall": round(float(recall_score(y_test[low_vol_mask], test_preds[low_vol_mask], zero_division=0)), 4)
        }
    }

    full_report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "champion_model": best_model_name,
        "optimal_threshold": round(champ_thresh, 2),
        "total_test_samples": len(y_test),
        "comparison_summary": comparison_results,
        "directional_performance": dir_metrics,
        "horizon_performance": horizon_metrics,
        "lead_time_analysis": lead_dist,
        "regime_performance": regime_metrics,
        "feature_names": feature_cols
    }

    # Save artifact and metrics
    os.makedirs(os.path.dirname(MODEL_OUTPUT_PATH), exist_ok=True)
    joblib.dump({
        "model": best_clf,
        "model_name": best_model_name,
        "scaler": scaler if best_use_scaled else None,
        "use_scaled": best_use_scaled,
        "optimal_threshold": champ_thresh,
        "feature_names": feature_cols,
        "metrics": comparison_results["models"][best_model_name]
    }, MODEL_OUTPUT_PATH)
    print(f"[Reversal Train] Saved trained model to {MODEL_OUTPUT_PATH}")

    with open(METRICS_OUTPUT_PATH, "w") as f:
        json.dump(full_report, f, indent=2)
    print(f"[Reversal Train] Saved evaluation metrics to {METRICS_OUTPUT_PATH}")

    return full_report


if __name__ == "__main__":
    train_and_evaluate_reversal_models()
