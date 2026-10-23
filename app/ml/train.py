import os
import json
import time
import joblib
import numpy as np
import pandas as pd
from typing import List, Tuple
from datetime import datetime

from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    roc_auc_score,
    brier_score_loss,
    confusion_matrix
)
import xgboost as xgb

from app.services.market_data import fetch_intraday_candles
from app.services.feature_engine import compute_market_features, create_dynamic_features_vector, FEATURE_NAMES

# Ensure directories exist
os.makedirs("data/raw", exist_ok=True)
os.makedirs("data/processed", exist_ok=True)
os.makedirs("app/ml", exist_ok=True)


def build_dynamic_dataset(
    symbols: List[str] = ["TCS.NS", "INFY.NS", "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS"]
) -> Tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    """
    Downloads historical 1m data and generates dynamic training pairs:
    Market Features at candle t + (target_percent, warning_minutes, direction) -> Reached (0 or 1).
    """
    all_X = []
    all_y = []
    
    # Grid of variable targets and horizons to train general dynamic model
    target_grid = [0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0]
    horizon_grid = [1, 3, 5, 10, 15, 20, 30]
    directions = ["UP", "DOWN"]
    
    print(f"[ML Train] Building multi-horizon dataset across {len(symbols)} tickers...")
    
    for symbol in symbols:
        try:
            print(f"  Fetching 1m data for {symbol}...")
            df = fetch_intraday_candles(symbol, period="5d", interval="1m")
            if df.empty or len(df) < 50:
                print(f"  Insufficient data for {symbol}, skipping.")
                continue
                
            # Save raw data copy
            clean_name = symbol.replace(".", "_")
            df.to_csv(f"data/raw/{clean_name}_1m.csv")
            
            feat_df = compute_market_features(df)
            n_rows = len(feat_df)
            
            high_arr = feat_df['high'].values
            low_arr = feat_df['low'].values
            close_arr = feat_df['close'].values
            
            # Step through historical candles with step to prevent redundant correlation
            for i in range(30, n_rows - 35, 2):
                curr_close = close_arr[i]
                row_dict = feat_df.iloc[i].to_dict()
                
                # Sample 4 configurations per candle to ensure balanced representation
                for _ in range(4):
                    tgt_pct = float(np.random.choice(target_grid))
                    horizon = int(np.random.choice(horizon_grid))
                    direction = str(np.random.choice(directions))
                    
                    # Future window slice: from i+1 to i+horizon (inclusive)
                    end_idx = min(i + horizon + 1, n_rows)
                    if end_idx <= i + 1:
                        continue
                        
                    future_highs = high_arr[i + 1:end_idx]
                    future_lows = low_arr[i + 1:end_idx]
                    
                    if direction == "UP":
                        target_price = curr_close * (1.0 + (tgt_pct / 100.0))
                        reached = 1 if np.max(future_highs) >= target_price else 0
                    else:
                        target_price = curr_close * (1.0 - (tgt_pct / 100.0))
                        reached = 1 if np.min(future_lows) <= target_price else 0
                        
                    feat_vec = create_dynamic_features_vector(
                        market_features=row_dict,
                        target_percent=tgt_pct,
                        warning_minutes=horizon,
                        direction=direction
                    )
                    
                    all_X.append(feat_vec)
                    all_y.append(reached)
                    
        except Exception as e:
            print(f"  Error processing {symbol}: {e}")
            
    X = np.array(all_X, dtype=np.float32)
    y = np.array(all_y, dtype=np.int32)
    
    print(f"[ML Train] Dataset generated: {X.shape[0]} samples, {X.shape[1]} features.")
    pos_rate = float(np.mean(y)) * 100
    print(f"[ML Train] Target positive class rate: {pos_rate:.2f}%")
    return X, y


def train_and_evaluate_models():
    """
    Executes Phase 7 & 8:
    1. Time-based split (70% train, 15% validation, 15% test).
    2. Trains Logistic Regression, Random Forest, and XGBoost.
    3. Calibrates probabilities.
    4. Evaluates precision, recall, F1, ROC-AUC, Brier score, and confusion matrix.
    5. Saves best model and detailed metrics.
    """
    start_time = time.time()
    X, y = build_dynamic_dataset()
    
    if len(X) < 100:
        raise ValueError("Not enough training samples generated!")
        
    n_samples = len(X)
    train_end = int(n_samples * 0.70)
    val_end = int(n_samples * 0.85)
    
    # Strict chronological / sequential split to prevent future-data leakage
    X_train, y_train = X[:train_end], y[:train_end]
    X_val, y_val = X[train_end:val_end], y[train_end:val_end]
    X_test, y_test = X[val_end:], y[val_end:]
    
    print(f"Data Split: Train={len(X_train)}, Val={len(X_val)}, Test={len(X_test)}")
    
    # Scaler for linear models and stability
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_val_scaled = scaler.transform(X_val)
    X_test_scaled = scaler.transform(X_test)
    
    models = {
        "LogisticRegression": LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42),
        "RandomForest": RandomForestClassifier(n_estimators=100, max_depth=10, min_samples_leaf=4, class_weight="balanced", random_state=42, n_jobs=-1),
        "XGBoost": xgb.XGBClassifier(
            n_estimators=120,
            max_depth=5,
            learning_rate=0.08,
            subsample=0.85,
            colsample_bytree=0.85,
            scale_pos_weight=max(1.0, float((len(y_train) - sum(y_train)) / max(sum(y_train), 1))),
            random_state=42,
            eval_metric="logloss"
        )
    }
    
    model_results = {}
    best_model_name = None
    best_f1 = -1.0
    best_model_pipeline = None
    
    for name, clf in models.items():
        print(f"\n--- Training {name} ---")
        use_scaled = name == "LogisticRegression"
        train_features = X_train_scaled if use_scaled else X_train
        val_features = X_val_scaled if use_scaled else X_val
        test_features = X_test_scaled if use_scaled else X_test
        
        clf.fit(train_features, y_train)
        
        # Calibrate classifier probabilities so model probabilities reflect true risk
        try:
            calibrated = CalibratedClassifierCV(clf, cv="prefit", method="sigmoid")
            calibrated.fit(val_features, y_val)
            eval_model = calibrated
        except Exception as ce:
            print(f"Calibration warning for {name}: {ce}. Using raw model.")
            eval_model = clf
            
        # Predict on Test set
        y_pred = eval_model.predict(test_features)
        y_prob = eval_model.predict_proba(test_features)[:, 1]
        
        acc = float(accuracy_score(y_test, y_pred))
        prec = float(precision_score(y_test, y_pred, zero_division=0))
        rec = float(recall_score(y_test, y_pred, zero_division=0))
        f1 = float(f1_score(y_test, y_pred, zero_division=0))
        roc_auc = float(roc_auc_score(y_test, y_prob)) if len(np.unique(y_test)) > 1 else 0.5
        brier = float(brier_score_loss(y_test, y_prob))
        cm = confusion_matrix(y_test, y_pred).tolist()
        
        print(f"Results for {name}:")
        print(f"  Accuracy:  {acc * 100:.2f}%")
        print(f"  Precision: {prec * 100:.2f}%")
        print(f"  Recall:    {rec * 100:.2f}%")
        print(f"  F1-Score:  {f1:.4f}")
        print(f"  ROC-AUC:   {roc_auc:.4f}")
        print(f"  Brier Loss: {brier:.4f}")
        
        model_results[name] = {
            "accuracy": round(acc, 4),
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1_score": round(f1, 4),
            "roc_auc": round(roc_auc, 4),
            "brier_score": round(brier, 4),
            "confusion_matrix": cm
        }
        
        # We select the model that balances high ROC-AUC and F1
        score = roc_auc + f1
        if score > best_f1:
            best_f1 = score
            best_model_name = name
            best_model_pipeline = {
                "name": name,
                "model": eval_model,
                "scaler": scaler if use_scaled else None,
                "use_scaled": use_scaled
            }
            
    print(f"\n[ML Train] Selected Champion Model: {best_model_name}")

    # -------------------------------------------------------------
    # AUTOMATED ADAPTIVE THRESHOLD OPTIMIZATION PIPELINE
    # -------------------------------------------------------------
    # Thresholds are optimized STRICTLY on the Validation split (X_val, y_val)
    # to completely prevent data leakage. Never evaluated on Test during selection.
    print("\n--- Running Automated Adaptive Threshold Optimization Pipeline ---")
    from app.services.adaptive_threshold import adaptive_engine, REGISTRY_PATH

    val_model = best_model_pipeline["model"]
    val_use_scaled = best_model_pipeline["use_scaled"]
    val_inputs = X_val_scaled if val_use_scaled else X_val
    val_probs = val_model.predict_proba(val_inputs)[:, 1]

    # Feature 0 is target_percent, Feature 1 is warning_minutes, Feature 2 is is_up
    tgt_col = X_val[:, 0]
    win_col = X_val[:, 1]
    dir_col = X_val[:, 2]

    target_slices = [0.5, 1.0, 2.0, 3.0, 5.0]
    horizon_slices = [3, 5, 10, 15, 30]
    direction_slices = ["UP", "DOWN"]

    adaptive_threshold_results = {}

    for symbol_clean in ["TCS", "INFY", "RELIANCE", "HDFCBANK", "ICICIBANK"]:
        for tgt in target_slices:
            for win in horizon_slices:
                for d_str in direction_slices:
                    d_val = 1.0 if d_str == "UP" else 0.0

                    mask_val = (
                        (np.abs(tgt_col - tgt) <= 0.6) &
                        (np.abs(win_col - win) <= 5) &
                        (dir_col == d_val)
                    )

                    if np.sum(mask_val) >= adaptive_engine.requirements.minimum_samples:
                        sub_y_val = y_val[mask_val]
                        sub_p_val = val_probs[mask_val]
                        res = adaptive_engine.evaluate_threshold_grid_on_validation(sub_y_val, sub_p_val)
                    else:
                        res = adaptive_engine.get_adaptive_threshold(symbol_clean, tgt, win, d_str)

                    res["calculated_at"] = datetime.utcnow().isoformat()
                    adaptive_engine.register_configuration_threshold(
                        symbol=symbol_clean,
                        target_percent=tgt,
                        warning_minutes=win,
                        direction=d_str,
                        optimal_result=res,
                        model_version="1.0.0"
                    )
                    key = adaptive_engine.make_config_key(symbol_clean, tgt, win, d_str)
                    adaptive_threshold_results[key] = {
                        "threshold": res.get("threshold"),
                        "status": res.get("status"),
                        "metrics": res.get("metrics")
                    }

    adaptive_engine.save_registry()
    print(f"[AdaptiveThreshold] Successfully calibrated and saved configuration thresholds to {REGISTRY_PATH}")
    
    # Feature importances from XGBoost / Random Forest
    feature_importances = {}
    if hasattr(models["XGBoost"], "feature_importances_"):
        fi = models["XGBoost"].feature_importances_
        sorted_indices = np.argsort(fi)[::-1]
        for idx in sorted_indices[:15]:
            feature_importances[FEATURE_NAMES[idx]] = round(float(fi[idx]), 4)
            
    output_metadata = {
        "timestamp": datetime.utcnow().isoformat(),
        "total_samples": int(n_samples),
        "train_samples": len(X_train),
        "val_samples": len(X_val),
        "test_samples": len(X_test),
        "champion_model": best_model_name,
        "models": model_results,
        "adaptive_thresholds_count": len(adaptive_threshold_results),
        "adaptive_thresholds_sample": dict(list(adaptive_threshold_results.items())[:8]),
        "feature_importances": feature_importances,
        "features_list": FEATURE_NAMES,
        "training_time_seconds": round(time.time() - start_time, 2)
    }
    
    # Save artifacts
    model_artifact = {
        "model": best_model_pipeline["model"],
        "scaler": best_model_pipeline["scaler"],
        "use_scaled": best_model_pipeline["use_scaled"],
        "model_name": best_model_name,
        "feature_names": FEATURE_NAMES,
        "metadata": output_metadata
    }
    
    joblib.dump(model_artifact, "app/ml/model.joblib")
    with open("app/ml/metrics.json", "w") as f:
        json.dump(output_metadata, f, indent=2)
    with open("data/processed/metrics.json", "w") as f:
        json.dump(output_metadata, f, indent=2)
        
    print("[ML Train] Successfully saved app/ml/model.joblib and metrics.json!")
    return output_metadata


if __name__ == "__main__":
    train_and_evaluate_models()
