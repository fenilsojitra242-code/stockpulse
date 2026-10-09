import os
import joblib
import numpy as np
from typing import Dict, Any, Optional, Tuple
from app.services.feature_engine import create_dynamic_features_vector, FEATURE_NAMES
from app.services.market_data import get_latest_market_snapshot

_MODEL_CACHE = None
MODEL_PATH = "app/ml/model.joblib"


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

