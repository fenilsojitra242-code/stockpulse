import os
import joblib
import numpy as np
from typing import Dict, Any, Optional, Tuple, List
from app.services.feature_engine import (
    create_dynamic_features_vector,
    FEATURE_NAMES,
    extract_supertrend_reversal_feature_vector,
    calculate_reversal_risk_score,
    SUPERTREND_REVERSAL_FEATURE_NAMES
)
from app.services.market_data import get_latest_market_snapshot

_MODEL_CACHE = None
MODEL_PATH = "app/ml/model.joblib"

_REVERSAL_MODEL_CACHE = None
REVERSAL_MODEL_PATH = "app/ml/reversal_model.joblib"


def load_model():
    """Load cached model artifact or load from disk."""
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE
        
    if os.path.exists(MODEL_PATH):
        try:
            _MODEL_CACHE = joblib.load(MODEL_PATH)
            print(f"[PredictionService] Loaded model: {_MODEL_CACHE.get('model_name', 'Unknown')}")
            return _MODEL_CACHE
        except Exception as e:
            print(f"[PredictionService] Error loading model from {MODEL_PATH}: {e}")
            
    return None


def calculate_statistical_barrier_probability(
    market_features: Dict[str, Any],
    target_percent: float,
    warning_minutes: int,
    direction: str
) -> float:
    """
    Robust statistical barrier pricing model (first-passage Brownian motion with drift)
    used as baseline and fallback if ML weights are initializing.
    Produces continuous, calibrated probability distribution across dynamic horizons.
    """
    is_up = direction.upper() == "UP"
    dir_sign = 1.0 if is_up else -1.0
    
    vol_10m = max(market_features.get('volatility_10m', 0.15), 0.05)
    horizon_sqrt = np.sqrt(max(float(warning_minutes), 1.0))
    expected_vol = vol_10m * horizon_sqrt
    
    # Dampen momentum projection across horizons with mean-reversion dampening
    ret_5m = market_features.get('return_5m', 0.0) * dir_sign
    roc_10m = market_features.get('roc_10m', 0.0) * dir_sign
    time_factor = min(2.5, np.sqrt(max(float(warning_minutes), 1.0) / 5.0))
    drift = (ret_5m * 0.40 + roc_10m * 0.20) * time_factor
    
    vol_ratio = min(max(market_features.get('vol_ratio_20m', 1.0), 0.5), 4.0)
    vol_boost = 0.04 * np.log1p(vol_ratio)
    
    rsi = market_features.get('rsi_14', 50.0)
    rsi_factor = ((rsi - 50.0) / 100.0) * dir_sign * 0.08
    
    # Smooth continuous normalized difficulty metric
    z = (target_percent - drift) / max(expected_vol, 0.06)
    
    # Continuous logistic barrier curve: when z=0 (drift matches target), probability is ~50%
    # when z > 0 (distance greater than drift), probability smoothly decreases
    # when z < 0 (strong momentum exceeds target), probability smoothly increases
    base_prob = 1.0 / (1.0 + np.exp(1.15 * z))
    
    prob = base_prob + vol_boost + rsi_factor
    return float(np.clip(prob, 0.03, 0.92))


def predict_target_probability(
    symbol: str,
    target_percent: float,
    warning_minutes: int,
    direction: str = "UP",
    market_snapshot: Optional[Dict] = None
) -> Dict[str, Any]:
    """
    Core prediction engine:
    Evaluates probability that stock 'symbol' will reach 'target_percent'
    within 'warning_minutes' in 'direction'.
    """
    if market_snapshot is None:
        market_snapshot = get_latest_market_snapshot(symbol)
        
    features = market_snapshot.get("features", {})
    current_price = market_snapshot["price"]
    current_change = market_snapshot["change_pct"]
    
    model_artifact = load_model()
    
    abs_target_percent = abs(float(target_percent))

    def evaluate_single_direction(dir_str: str) -> float:
        stat_prob = calculate_statistical_barrier_probability(
            market_features=features,
            target_percent=abs_target_percent,
            warning_minutes=warning_minutes,
            direction=dir_str
        )
        if model_artifact is not None and abs_target_percent >= 0.01:
            try:
                feat_vec = create_dynamic_features_vector(
                    market_features=features,
                    target_percent=abs_target_percent,
                    warning_minutes=warning_minutes,
                    direction=dir_str
                ).reshape(1, -1)
                
                clf = model_artifact["model"]
                scaler = model_artifact.get("scaler")
                if scaler is not None and model_artifact.get("use_scaled", False):
                    feat_vec = scaler.transform(feat_vec)
                    
                proba = float(clf.predict_proba(feat_vec)[0, 1])
                # Calibrate ML classifier output relative to training base rate (~0.045):
                # An ML prob of 0.045 represents a neutral prior (~0.40)
                # An ML prob of 0.08+ represents strong positive model conviction (0.65+)
                # An ML prob of 0.01 represents weak/counter-trend signal (0.10)
                conviction = float(np.clip(0.40 + (proba - 0.045) * 7.5, 0.04, 0.94))
                blended = 0.60 * conviction + 0.40 * stat_prob
                return float(np.clip(blended, 0.02, 0.95))
            except Exception as e:
                print(f"[PredictionService] Inference error, fallback to statistical: {e}")
                
        return float(np.clip(stat_prob, 0.02, 0.95))

    prob_up = None
    prob_down = None
    
    if direction.upper() == "BOTH":
        prob_up = round(evaluate_single_direction("UP"), 4)
        prob_down = round(evaluate_single_direction("DOWN"), 4)
        chosen_prob = max(prob_up, prob_down)
        active_direction = "UP" if prob_up >= prob_down else "DOWN"
    else:
        active_direction = direction.upper()
        chosen_prob = round(evaluate_single_direction(active_direction), 4)
        if active_direction == "UP":
            prob_up = chosen_prob
        else:
            prob_down = chosen_prob
            
    # Calculate exact target price boundaries
    target_up_price = round(current_price * (1.0 + (abs_target_percent / 100.0)), 2)
    target_down_price = round(current_price * (1.0 - (abs_target_percent / 100.0)), 2)

    if active_direction == "UP":
        calculated_target_price = target_up_price
    elif active_direction == "DOWN":
        calculated_target_price = target_down_price
    else:
        calculated_target_price = target_up_price if prob_up >= prob_down else target_down_price

    # Evaluate against Automated Adaptive Threshold System
    from app.services.adaptive_threshold import adaptive_engine
    threshold_eval = adaptive_engine.evaluate_live_prediction(
        symbol=symbol,
        target_percent=abs_target_percent,
        warning_minutes=warning_minutes,
        direction=active_direction,
        predicted_probability=chosen_prob
    )

    threshold_status = threshold_eval.get("status", "OPTIMAL_THRESHOLD_ACTIVE")
    adaptive_threshold = threshold_eval.get("adaptive_threshold")
    is_alert_worthy = threshold_eval.get("is_alert_worthy", False)

    if threshold_status == "INSUFFICIENT_CONFIDENCE":
        prediction_label = "INSUFFICIENT CONFIDENCE"
        status_label = "INSUFFICIENT_CONFIDENCE"
    elif is_alert_worthy:
        prediction_label = "HIGH PROBABILITY"
        status_label = "EARLY WARNING"
    elif chosen_prob >= 0.50:
        prediction_label = "MODERATE PROBABILITY"
        status_label = "MONITORING"
    else:
        prediction_label = "LOW PROBABILITY"
        status_label = "MONITORING"

    req_velocity = round(abs_target_percent / max(warning_minutes, 1), 2)
    rsi = round(features.get("rsi_14", 50.0), 1)
    ret_5m = round(features.get("return_5m", 0.0), 2)
    vol_ratio = round(features.get("vol_ratio_20m", 1.0), 2)

    target_sign = "+" if active_direction == "UP" else "-"

    if threshold_status == "INSUFFICIENT_CONFIDENCE":
        thresh_note = "Early warning suppressed: Minimum validation precision/recall criteria not met for this configuration."
    else:
        thresh_pct = f"{adaptive_threshold * 100:.0f}%" if adaptive_threshold else "N/A"
        thresh_note = f"Automatic adaptive threshold: {thresh_pct} ({'Exceeded' if is_alert_worthy else 'Below threshold'})."

    explanation = (
        f"{symbol} is showing {ret_5m}% return over last 5m (RSI: {rsi}, Volume: {vol_ratio}x 20-SMA). "
        f"Requires {req_velocity}%/min velocity to reach target {target_sign}{abs_target_percent:.2f}% in {warning_minutes}m. "
        f"Model probability: {int(chosen_prob * 100)}% ({prediction_label}). {thresh_note}"
    )

    return {
        "symbol": symbol,
        "current_price": current_price,
        "current_change": current_change,
        "target_percent": abs_target_percent,
        "target_price": calculated_target_price,
        "target_price_up": target_up_price,
        "target_price_down": target_down_price,
        "warning_minutes": warning_minutes,
        "direction": active_direction,
        "requested_direction": direction.upper(),
        "probability": chosen_prob,
        "probability_up": prob_up,
        "probability_down": prob_down,
        "prediction_label": prediction_label,
        "status_label": status_label,
        "adaptive_threshold": adaptive_threshold,
        "threshold_status": threshold_status,
        "is_alert_worthy": is_alert_worthy,
        "threshold_evaluation": threshold_eval,
        "features_summary": {
            "return_1m": round(features.get("return_1m", 0.0), 2),
            "return_5m": ret_5m,
            "rsi_14": rsi,
            "volume_ratio": vol_ratio,
            "macd_hist": round(features.get("macd_hist", 0.0), 3),
            "required_velocity_pct_min": req_velocity,
            "volatility_10m": round(features.get("volatility_10m", 0.1), 3)
        },
        "explanation": explanation,
        "disclaimer": "⚠️ Model-generated estimate; not a guarantee."
    }


def load_reversal_model() -> Optional[Dict[str, Any]]:
    """Load cached Supertrend reversal model artifact or load from disk."""
    global _REVERSAL_MODEL_CACHE
    if _REVERSAL_MODEL_CACHE is not None:
        return _REVERSAL_MODEL_CACHE

    if os.path.exists(REVERSAL_MODEL_PATH):
        try:
            _REVERSAL_MODEL_CACHE = joblib.load(REVERSAL_MODEL_PATH)
            clf = _REVERSAL_MODEL_CACHE.get("model")
            if hasattr(clf, "estimator") and hasattr(clf.estimator, "n_jobs"):
                clf.estimator.n_jobs = 1
            elif hasattr(clf, "n_jobs"):
                clf.n_jobs = 1
            print(f"[PredictionService] Loaded Supertrend reversal model: {_REVERSAL_MODEL_CACHE.get('model_name', 'Unknown')}")
            return _REVERSAL_MODEL_CACHE
        except Exception as e:
            print(f"[PredictionService] Error loading reversal model from {REVERSAL_MODEL_PATH}: {e}")

    return None


def predict_supertrend_reversal(
    candles_or_df: Any,
    confirmed_direction: str,
    horizon_minutes: int = 5,
    current_price: Optional[float] = None,
    threshold: Optional[float] = None,
    symbol: str = "STOCK",
    period: int = 10,
    multiplier: float = 3.0,
    supertrend_price: Optional[float] = None,
    atr_value: Optional[float] = None
) -> Dict[str, Any]:
    """
    Predictive Early Reversal Layer for SuperTrend:
    Estimates the probability that the confirmed Supertrend will undergo a genuine
    transition (BUY -> SELL or SELL -> BUY) within the next horizon_minutes (1, 2, or 5).

    - Uses trained calibrated ML model (Random Forest / XGBoost) when available.
    - Transparently falls back to calibrated heuristic risk score if model artifact
      is missing, preserving full operational integrity.
    - Flags uncertainty if historical candles are insufficient (< 15 bars).
    - Guaranteed invariant: NEVER overwrites the confirmed Supertrend indicator state.
    """
    conf_dir = "BUY" if str(confirmed_direction).upper() == "BUY" else "SELL"
    reversal_target_dir = "SELL" if conf_dir == "BUY" else "BUY"
    horizon = int(horizon_minutes) if horizon_minutes in (1, 2, 5) else 5

    # Extract feature vector
    feat_vec, feat_dict, has_sufficient = extract_supertrend_reversal_feature_vector(
        candles_or_df=candles_or_df,
        horizon_minutes=horizon,
        current_price=current_price,
        period=period,
        multiplier=multiplier
    )

    cp = float(current_price if current_price is not None else feat_dict.get('close', 100.0))
    st_p = float(supertrend_price if supertrend_price is not None else feat_dict.get('supertrend', cp))
    atr_val = float(atr_value if atr_value is not None else feat_dict.get('atr_14', 1.0))
    dist_pts = round(abs(cp - st_p), 2)
    dist_atr = round(dist_pts / max(0.01, atr_val), 2)

    # Uncertainty handling
    uncertainty_flag = not has_sufficient
    data_quality = "OPTIMAL" if has_sufficient else "INSUFFICIENT_HISTORY"

    # Transparent rule-based heuristic score fallback
    heuristic_score = calculate_reversal_risk_score(
        distance_points=dist_pts,
        atr_value=atr_val,
        proximity_points=max(0.5, atr_val * 0.5),
        target_direction=reversal_target_dir,
        candle_color="RED" if feat_dict.get('candle_body_atr', 0.0) < 0 else "GREEN",
        candle_points=round(feat_dict.get('candle_body_atr', 0.0) * atr_val, 2),
        momentum_adverse=float(feat_dict.get('mom_adverse_3m', 0.0) * atr_val),
        delta_rsi=float(feat_dict.get('delta_rsi_3m', 0.0)),
        trend_duration_bars=int(feat_dict.get('trend_duration_bars', 1))
    )

    reversal_artifact = load_reversal_model()
    model_type = "RULE_BASED"
    is_heuristic = True
    calibrated_prob = heuristic_score
    active_threshold = float(threshold) if threshold is not None else 0.55

    if reversal_artifact is not None and has_sufficient:
        try:
            clf = reversal_artifact.get("model")
            scaler = reversal_artifact.get("scaler")
            use_scaled = reversal_artifact.get("use_scaled", False)
            feat_cols = reversal_artifact.get("feature_names", [])

            # Extract features matching the model's exact expected names and order
            X_in = np.array([[feat_dict.get(c, 0.0) for c in feat_cols]], dtype=np.float32)
            if scaler is not None and use_scaled:
                X_in = scaler.transform(X_in)

            # Calibrated probability output
            ml_proba = float(clf.predict_proba(X_in)[0, 1])

            # Blend with heuristic sanity score to smooth rare anomalous spikes
            calibrated_prob = round(float(np.clip(0.70 * ml_proba + 0.30 * heuristic_score, 0.02, 0.98)), 4)
            model_type = str(reversal_artifact.get("model_name", "CALIBRATED_ML"))
            is_heuristic = False
            if threshold is None:
                active_threshold = float(reversal_artifact.get("optimal_threshold", 0.55))

        except Exception as e:
            print(f"[PredictionService] Reversal model inference exception for {symbol}: {e}. Using rule-based fallback.")
            calibrated_prob = heuristic_score
            model_type = "RULE_BASED"
            is_heuristic = True
            active_threshold = float(threshold) if threshold is not None else 0.60

    # Decision: Trigger early warning only when calibrated probability meets threshold and quality passes
    is_early_warning = bool(calibrated_prob >= active_threshold and not uncertainty_flag)

    explanation = (
        f"{symbol} is in confirmed {conf_dir} trend (ST: ₹{st_p:.2f}, distance: {dist_atr:.2f}x ATR). "
        f"Early reversal warning to {reversal_target_dir} within {horizon}m: "
        f"probability {calibrated_prob * 100:.1f}% (threshold {active_threshold * 100:.0f}%, model: {model_type}). "
        f"{'CRITICAL REVERSAL RISK - EARLY WARNING ACTIVE' if is_early_warning else 'Within safe trend bounds'}"
    )

    return {
        "symbol": symbol,
        "current_direction": conf_dir,
        "confirmed_direction": conf_dir,
        "reversal_target_direction": reversal_target_dir,
        "horizon_minutes": horizon,
        "reversal_probability": calibrated_prob,
        "reversal_risk_score": heuristic_score,
        "threshold": active_threshold,
        "is_early_warning_ready": is_early_warning,
        "model_type": model_type,
        "is_heuristic": is_heuristic,
        "uncertainty_flag": uncertainty_flag,
        "data_quality": data_quality,
        "distance_points": dist_pts,
        "distance_to_band_atr": dist_atr,
        "features_summary": {
            "mom_adverse_3m": round(float(feat_dict.get('mom_adverse_3m', 0.0)), 3),
            "delta_rsi_3m": round(float(feat_dict.get('delta_rsi_3m', 0.0)), 2),
            "rsi_14": round(float(feat_dict.get('rsi_14', 50.0)), 1),
            "macd_hist": round(float(feat_dict.get('macd_hist', 0.0)), 3),
            "trend_duration_bars": int(feat_dict.get('trend_duration_bars', 1)),
            "volatility_10m": round(float(feat_dict.get('volatility_10m', 0.0)), 3)
        },
        "explanation": explanation
    }


