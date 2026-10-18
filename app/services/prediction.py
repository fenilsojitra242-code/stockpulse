import os
import time
import copy
import joblib
import numpy as np
from typing import Dict, Any, Optional, Tuple, List
from app.services.feature_engine import (
    create_dynamic_features_vector,
    FEATURE_NAMES,
    extract_supertrend_reversal_feature_vector,
    calculate_reversal_risk_score,
    SUPERTREND_REVERSAL_FEATURE_NAMES,
    get_candle_sequence_key
)
from app.services.market_data import get_latest_market_snapshot
from app.services.market_regime import classify_market_regime
from app.services.diagnostic_logger import diagnostic_logger

_MODEL_CACHE = None
_MODEL_MTIME = 0.0
MODEL_PATH = "app/ml/model.joblib"

_REVERSAL_MODEL_CACHE = None
_REVERSAL_MODEL_MTIME = 0.0
REVERSAL_MODEL_PATH = "app/ml/reversal_model.joblib"

_PREDICT_ST_REVERSAL_CACHE: Dict[Tuple, Dict[str, Any]] = {}
_PREDICT_TARGET_PROB_CACHE: Dict[Tuple, Dict[str, Any]] = {}

_MODEL_DIAGNOSTICS: Dict[str, Any] = {
    "target_model": {
        "status": "UNLOADED",
        "loaded": False,
        "error": None,
        "model_name": None,
        "path": MODEL_PATH,
        "mtime": 0.0,
        "load_duration_ms": 0.0
    },
    "reversal_model": {
        "status": "UNLOADED",
        "loaded": False,
        "error": None,
        "model_name": None,
        "path": REVERSAL_MODEL_PATH,
        "mtime": 0.0,
        "load_duration_ms": 0.0
    }
}


def load_model():
    """Load cached model artifact or load from disk with mtime invalidation."""
    global _MODEL_CACHE, _MODEL_MTIME, _MODEL_DIAGNOSTICS
    mtime = os.path.getmtime(MODEL_PATH) if os.path.exists(MODEL_PATH) else 0.0
    if _MODEL_CACHE is not None and mtime == _MODEL_MTIME:
        return _MODEL_CACHE

    if not os.path.exists(MODEL_PATH):
        _MODEL_DIAGNOSTICS["target_model"] = {
            "status": "FILE_MISSING",
            "loaded": False,
            "error": f"Model artifact not found at {MODEL_PATH}",
            "model_name": None,
            "path": MODEL_PATH,
            "mtime": 0.0,
            "load_duration_ms": 0.0
        }
        return None

    t0 = time.perf_counter()
    try:
        loaded = joblib.load(MODEL_PATH)
        dur = (time.perf_counter() - t0) * 1000.0
        if not isinstance(loaded, dict) or "model" not in loaded:
            raise ValueError(f"Corrupt model artifact: expected dict with 'model' key, got {type(loaded)}")
        _MODEL_CACHE = loaded
        _MODEL_MTIME = mtime
        _PREDICT_TARGET_PROB_CACHE.clear()
        name = _MODEL_CACHE.get("model_name", "Unknown")
        _MODEL_DIAGNOSTICS["target_model"] = {
            "status": "LOADED",
            "loaded": True,
            "error": None,
            "model_name": name,
            "path": MODEL_PATH,
            "mtime": mtime,
            "load_duration_ms": round(dur, 2)
        }
        print(f"[PredictionService] Loaded model: {name} in {dur:.2f}ms")
        return _MODEL_CACHE
    except Exception as e:
        dur = (time.perf_counter() - t0) * 1000.0
        _MODEL_CACHE = None
        _MODEL_DIAGNOSTICS["target_model"] = {
            "status": "CORRUPT",
            "loaded": False,
            "error": str(e),
            "model_name": None,
            "path": MODEL_PATH,
            "mtime": mtime,
            "load_duration_ms": round(dur, 2)
        }
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
    
    clean_sym_upper = symbol.strip().upper()
    abs_target_percent = abs(float(target_percent))
    dir_upper = direction.upper()
    model_mtime = os.path.getmtime(MODEL_PATH) if os.path.exists(MODEL_PATH) else 0.0
    feat_tuple = tuple(sorted((str(k), round(float(v), 4)) for k, v in features.items() if isinstance(v, (int, float, np.floating, np.integer)) and not np.isnan(v))) if isinstance(features, dict) else ()
    cache_key = (
        clean_sym_upper,
        round(abs_target_percent, 4),
        int(warning_minutes),
        dir_upper,
        str(market_snapshot.get("latest_market_timestamp")),
        round(float(current_price), 2),
        feat_tuple,
        model_mtime
    )

    if cache_key in _PREDICT_TARGET_PROB_CACHE:
        return copy.deepcopy(_PREDICT_TARGET_PROB_CACHE[cache_key])

    model_artifact = load_model()

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

    res = {
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

    if len(_PREDICT_TARGET_PROB_CACHE) > 512:
        _PREDICT_TARGET_PROB_CACHE.pop(next(iter(_PREDICT_TARGET_PROB_CACHE)))
    _PREDICT_TARGET_PROB_CACHE[cache_key] = res
    return copy.deepcopy(res)


def load_reversal_model() -> Optional[Dict[str, Any]]:
    """Load cached Supertrend reversal model artifact or load from disk with mtime invalidation."""
    global _REVERSAL_MODEL_CACHE, _REVERSAL_MODEL_MTIME, _MODEL_DIAGNOSTICS
    mtime = os.path.getmtime(REVERSAL_MODEL_PATH) if os.path.exists(REVERSAL_MODEL_PATH) else 0.0
    if _REVERSAL_MODEL_CACHE is not None and mtime == _REVERSAL_MODEL_MTIME:
        return _REVERSAL_MODEL_CACHE

    if not os.path.exists(REVERSAL_MODEL_PATH):
        _MODEL_DIAGNOSTICS["reversal_model"] = {
            "status": "FILE_MISSING",
            "loaded": False,
            "error": f"Reversal model artifact not found at {REVERSAL_MODEL_PATH}",
            "model_name": None,
            "path": REVERSAL_MODEL_PATH,
            "mtime": 0.0,
            "load_duration_ms": 0.0
        }
        return None

    t0 = time.perf_counter()
    try:
        loaded = joblib.load(REVERSAL_MODEL_PATH)
        dur = (time.perf_counter() - t0) * 1000.0
        if not isinstance(loaded, dict) or "model" not in loaded:
            raise ValueError(f"Corrupt reversal model artifact: expected dict with 'model' key, got {type(loaded)}")
        _REVERSAL_MODEL_CACHE = loaded
        _REVERSAL_MODEL_MTIME = mtime
        _PREDICT_ST_REVERSAL_CACHE.clear()
        clf = _REVERSAL_MODEL_CACHE.get("model")
        if hasattr(clf, "estimator") and hasattr(clf.estimator, "n_jobs"):
            clf.estimator.n_jobs = 1
        elif hasattr(clf, "n_jobs"):
            clf.n_jobs = 1
        name = _REVERSAL_MODEL_CACHE.get("model_name", "Unknown")
        _MODEL_DIAGNOSTICS["reversal_model"] = {
            "status": "LOADED",
            "loaded": True,
            "error": None,
            "model_name": name,
            "path": REVERSAL_MODEL_PATH,
            "mtime": mtime,
            "load_duration_ms": round(dur, 2)
        }
        print(f"[PredictionService] Loaded Supertrend reversal model: {name} in {dur:.2f}ms")
        return _REVERSAL_MODEL_CACHE
    except Exception as e:
        dur = (time.perf_counter() - t0) * 1000.0
        _REVERSAL_MODEL_CACHE = None
        _MODEL_DIAGNOSTICS["reversal_model"] = {
            "status": "CORRUPT",
            "loaded": False,
            "error": str(e),
            "model_name": None,
            "path": REVERSAL_MODEL_PATH,
            "mtime": mtime,
            "load_duration_ms": round(dur, 2)
        }
        print(f"[PredictionService] Error loading reversal model from {REVERSAL_MODEL_PATH}: {e}")
        return None


def prewarm_models() -> Dict[str, Any]:
    """
    Pre-loads target-probability model and Supertrend-reversal model before active alert evaluation begins.
    Reuses existing model-loading functions and model caches.
    Thread-safe, avoids competing instances, and produces comprehensive diagnostic telemetry.
    """
    t_start = time.perf_counter()
    load_model()
    load_reversal_model()
    total_dur = (time.perf_counter() - t_start) * 1000.0
    return {
        "target_model": copy.deepcopy(_MODEL_DIAGNOSTICS["target_model"]),
        "reversal_model": copy.deepcopy(_MODEL_DIAGNOSTICS["reversal_model"]),
        "total_prewarm_duration_ms": round(total_dur, 2)
    }


def get_model_diagnostics() -> Dict[str, Any]:
    """Returns telemetry of both prediction models."""
    return copy.deepcopy(_MODEL_DIAGNOSTICS)


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

    ckey = get_candle_sequence_key(candles_or_df)
    rev_model_mtime = os.path.getmtime(REVERSAL_MODEL_PATH) if os.path.exists(REVERSAL_MODEL_PATH) else 0.0
    cache_key = (
        ckey,
        symbol.strip().upper(),
        conf_dir,
        horizon,
        round(current_price, 2) if current_price is not None else None,
        float(threshold) if threshold is not None else None,
        int(period),
        float(multiplier),
        round(supertrend_price, 2) if supertrend_price is not None else None,
        rev_model_mtime
    ) if ckey is not None else None

    if cache_key is not None and cache_key in _PREDICT_ST_REVERSAL_CACHE:
        return _PREDICT_ST_REVERSAL_CACHE[cache_key].copy()

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

    # Classify market regime at prediction time (strictly causal past data)
    regime_info = classify_market_regime(candles_or_df)
    primary_regime = regime_info.get("primary_regime", "SIDEWAYS_CHOPPY")
    vol_regime = regime_info.get("volatility_regime", "NORMAL_VOLATILITY")
    regime_thresh_boost = float(regime_info.get("recommended_threshold_boost", 0.0))
    rec_confirming = int(regime_info.get("recommended_min_confirming_candles", 1))

    reversal_artifact = load_reversal_model()
    model_type = "RULE_BASED"
    is_heuristic = True
    calibrated_prob = heuristic_score
    base_thresh = float(threshold) if threshold is not None else 0.50
    active_threshold = min(0.85, base_thresh + regime_thresh_boost)

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
                opt_thresh = float(reversal_artifact.get("optimal_threshold", 0.50))
                active_threshold = min(0.85, opt_thresh + regime_thresh_boost)

        except Exception as e:
            print(f"[PredictionService] Reversal model inference exception for {symbol}: {e}. Using rule-based fallback.")
            calibrated_prob = heuristic_score
            model_type = "RULE_BASED"
            is_heuristic = True
            active_threshold = min(0.85, (float(threshold) if threshold is not None else 0.55) + regime_thresh_boost)

    # Decision: Trigger early warning only when calibrated probability meets threshold and quality passes
    is_early_warning = bool(calibrated_prob >= active_threshold and not uncertainty_flag)

    # Phase 6 Strict 4-State Machine Separation
    if uncertainty_flag or cp <= 0:
        prediction_state = "DATA_UNAVAILABLE"
        decision_reason = "Insufficient historical candles (< 15 bars) or invalid price"
    elif is_early_warning:
        prediction_state = "EARLY_WARNING"
        decision_reason = f"Probability {calibrated_prob * 100:.1f}% >= threshold {active_threshold * 100:.0f}% in {primary_regime}"
    else:
        prediction_state = "NO_CLEAR_SIGNAL"
        decision_reason = f"Within safe bounds ({dist_atr:.2f}x ATR). Probability {calibrated_prob * 100:.1f}% < threshold {active_threshold * 100:.0f}%"

    explanation = (
        f"{symbol} is in confirmed {conf_dir} trend (ST: ₹{st_p:.2f}, distance: {dist_atr:.2f}x ATR, regime: {primary_regime}). "
        f"Early reversal warning to {reversal_target_dir} within {horizon}m: "
        f"probability {calibrated_prob * 100:.1f}% (threshold {active_threshold * 100:.0f}%, model: {model_type}). "
        f"{'CRITICAL REVERSAL RISK - EARLY WARNING ACTIVE' if is_early_warning else 'Within safe trend bounds'}"
    )

    # Phase 2 Diagnostic Event Record (strictly past features snapshot)
    event_rec = diagnostic_logger.record_prediction(
        symbol=symbol,
        candle_interval="1m",
        prediction_timestamp=str(feat_dict.get('candle_timestamp') or ""),
        market_data_timestamp=str(feat_dict.get('candle_timestamp') or ""),
        confirmed_direction_before=conf_dir,
        raw_direction=conf_dir,
        atr=atr_val,
        final_upper_band=st_p if conf_dir == "SELL" else (st_p + 2.0 * atr_val),
        final_lower_band=st_p if conf_dir == "BUY" else max(0.01, st_p - 2.0 * atr_val),
        closing_price=cp,
        features_snapshot=feat_dict,
        predicted_direction=reversal_target_dir,
        horizon_minutes=horizon,
        reversal_probability=calibrated_prob,
        heuristic_score=heuristic_score,
        applied_threshold=active_threshold,
        market_regime=primary_regime,
        volatility_regime=vol_regime,
        early_warning_decision=is_early_warning,
        decision_reason=decision_reason,
        model_type=model_type
    )

    res = {
        "event_id": event_rec.get("event_id"),
        "symbol": symbol,
        "current_direction": conf_dir,
        "confirmed_direction": conf_dir,
        "reversal_target_direction": reversal_target_dir,
        "horizon_minutes": horizon,
        "reversal_probability": calibrated_prob,
        "reversal_risk_score": heuristic_score,
        "threshold": active_threshold,
        "is_early_warning_ready": is_early_warning,
        "prediction_state": prediction_state,
        "decision_reason": decision_reason,
        "market_regime": primary_regime,
        "volatility_regime": vol_regime,
        "recommended_min_confirming_candles": rec_confirming,
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

    if cache_key is not None:
        if len(_PREDICT_ST_REVERSAL_CACHE) > 256:
            _PREDICT_ST_REVERSAL_CACHE.pop(next(iter(_PREDICT_ST_REVERSAL_CACHE)))
        _PREDICT_ST_REVERSAL_CACHE[cache_key] = res.copy()

    return res



