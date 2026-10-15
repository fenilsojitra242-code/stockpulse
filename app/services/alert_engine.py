from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session

import json
from app.database.models import Alert, AlertHistory, SystemSetting
from app.services.market_data import get_latest_market_snapshot
from app.services.prediction import predict_target_probability, predict_supertrend_reversal
from app.services.adaptive_threshold import adaptive_engine
from app.services.messaging import notification_manager, NotificationEventType
from app.services.diagnostic_logger import diagnostic_logger
from app.services.market_regime import classify_market_regime


POPULAR_SUPERTREND_SYMBOLS = ["TCS", "INFY", "RELIANCE", "HDFCBANK", "TATAMOTORS", "SBIN", "ICICIBANK", "BHARTIARTL", "ITC", "WIPRO"]


def get_supertrend_settings(db: Session, user_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Retrieves user-specific SuperTrend indicator settings:
    - enabled: bool (default True)
    - proximity_points: float (default 1.0 pt before hit)
    - period: int (ATR period, default 10)
    - multiplier: float (ATR multiplier, default 3.0)
    - band_buffer_pct: float (hysteresis buffer % of ATR, default 0.0)
    - min_atr_distance_filter: float (minimum ATR distance filter, default 0.0)
    - min_confirming_candles: int (closed candles required to confirm reversal, default 1)
    - selected_symbols: list of stock symbols to monitor (exclusively!)
    """
    try:
        prefix = f"u{user_id}_" if user_id else ""
        row_enabled = db.query(SystemSetting).filter(SystemSetting.key == f"{prefix}supertrend_enabled").first()
        row_prox = db.query(SystemSetting).filter(SystemSetting.key == f"{prefix}supertrend_proximity_points").first()
        row_syms = db.query(SystemSetting).filter(SystemSetting.key == f"{prefix}supertrend_watchlist_symbols").first()
        row_period = db.query(SystemSetting).filter(SystemSetting.key == f"{prefix}supertrend_period").first()
        row_mult = db.query(SystemSetting).filter(SystemSetting.key == f"{prefix}supertrend_multiplier").first()
        row_buf = db.query(SystemSetting).filter(SystemSetting.key == f"{prefix}supertrend_band_buffer_pct").first()
        row_atr_f = db.query(SystemSetting).filter(SystemSetting.key == f"{prefix}supertrend_min_atr_filter").first()
        row_conf_c = db.query(SystemSetting).filter(SystemSetting.key == f"{prefix}supertrend_confirming_candles").first()

        # Fallback to defaults if user setting not explicitly configured yet
        if not row_enabled and user_id:
            row_enabled = db.query(SystemSetting).filter(SystemSetting.key == "supertrend_enabled").first()
        if not row_prox and user_id:
            row_prox = db.query(SystemSetting).filter(SystemSetting.key == "supertrend_proximity_points").first()
        if not row_period and user_id:
            row_period = db.query(SystemSetting).filter(SystemSetting.key == "supertrend_period").first()
        if not row_mult and user_id:
            row_mult = db.query(SystemSetting).filter(SystemSetting.key == "supertrend_multiplier").first()

        enabled = True if (not row_enabled or row_enabled.value.lower() == "true") else False
        prox = float(row_prox.value) if (row_prox and row_prox.value) else 1.0
        period = int(row_period.value) if (row_period and row_period.value) else 10
        multiplier = float(row_mult.value) if (row_mult and row_mult.value) else 3.0
        band_buffer_pct = float(row_buf.value) if (row_buf and row_buf.value) else 0.0
        min_atr_filter = float(row_atr_f.value) if (row_atr_f and row_atr_f.value) else 0.0
        min_confirming_candles = int(row_conf_c.value) if (row_conf_c and row_conf_c.value) else 1

        selected_symbols: List[str] = []
        if row_syms and row_syms.value is not None:
            try:
                decoded = json.loads(row_syms.value)
                if isinstance(decoded, list):
                    selected_symbols = [str(s).strip().upper() for s in decoded if s and isinstance(s, (str, int))]
            except Exception:
                selected_symbols = []

        return {
            "enabled": enabled,
            "proximity_points": prox,
            "period": period,
            "multiplier": multiplier,
            "band_buffer_pct": band_buffer_pct,
            "min_atr_distance_filter": min_atr_filter,
            "min_confirming_candles": min_confirming_candles,
            "selected_symbols": [s.upper() for s in selected_symbols],
            "popular_symbols": POPULAR_SUPERTREND_SYMBOLS
        }
    except Exception as e:
        print(f"[SuperTrend] Error fetching settings: {e}")
        return {
            "enabled": True,
            "proximity_points": 1.0,
            "period": 10,
            "multiplier": 3.0,
            "band_buffer_pct": 0.0,
            "min_atr_distance_filter": 0.0,
            "min_confirming_candles": 1,
            "selected_symbols": [],
            "popular_symbols": POPULAR_SUPERTREND_SYMBOLS
        }


def save_supertrend_settings(
    db: Session,
    enabled: Optional[bool] = None,
    proximity_points: Optional[float] = None,
    selected_symbols: Optional[List[str]] = None,
    user_id: Optional[int] = None,
    period: Optional[int] = None,
    multiplier: Optional[float] = None,
    band_buffer_pct: Optional[float] = None,
    min_atr_distance_filter: Optional[float] = None,
    min_confirming_candles: Optional[int] = None
) -> Dict[str, Any]:
    """
    Saves SuperTrend indicator settings scoped to the user into system_settings table.
    """
    prefix = f"u{user_id}_" if user_id else ""
    key_enabled = f"{prefix}supertrend_enabled"
    key_prox = f"{prefix}supertrend_proximity_points"
    key_syms = f"{prefix}supertrend_watchlist_symbols"
    key_period = f"{prefix}supertrend_period"
    key_mult = f"{prefix}supertrend_multiplier"
    key_buf = f"{prefix}supertrend_band_buffer_pct"
    key_atr_f = f"{prefix}supertrend_min_atr_filter"
    key_conf_c = f"{prefix}supertrend_confirming_candles"

    def _set_setting(key: str, val_str: str, desc: str):
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if not row:
            row = SystemSetting(key=key, value=val_str, description=desc)
            db.add(row)
        else:
            row.value = val_str

    try:
        if enabled is not None:
            _set_setting(key_enabled, "true" if enabled else "false", f"SuperTrend enabled for user {user_id}")

        if proximity_points is not None:
            prox_val = str(max(0.1, min(100.0, float(proximity_points))))
            _set_setting(key_prox, prox_val, f"Points before SuperTrend hit for user {user_id}")

        if period is not None:
            p_val = str(max(2, min(100, int(period))))
            _set_setting(key_period, p_val, f"SuperTrend ATR period for user {user_id}")

        if multiplier is not None:
            m_val = str(max(0.1, min(20.0, float(multiplier))))
            _set_setting(key_mult, m_val, f"SuperTrend ATR multiplier for user {user_id}")

        if band_buffer_pct is not None:
            b_val = str(max(0.0, min(50.0, float(band_buffer_pct))))
            _set_setting(key_buf, b_val, f"SuperTrend band buffer % for user {user_id}")

        if min_atr_distance_filter is not None:
            a_val = str(max(0.0, min(10.0, float(min_atr_distance_filter))))
            _set_setting(key_atr_f, a_val, f"SuperTrend min ATR distance filter for user {user_id}")

        if min_confirming_candles is not None:
            c_val = str(max(1, min(10, int(min_confirming_candles))))
            _set_setting(key_conf_c, c_val, f"SuperTrend confirming candles count for user {user_id}")

        if selected_symbols is not None:
            cleaned = []
            seen = set()
            for s in selected_symbols:
                c = s.strip().upper()
                if c and c not in seen:
                    cleaned.append(c)
                    seen.add(c)
            _set_setting(key_syms, json.dumps(cleaned), f"List of shares monitored for SuperTrend for user {user_id}")

        db.commit()
    except Exception as e:
        print(f"[SuperTrend] Error saving settings: {e}")
        db.rollback()

    return get_supertrend_settings(db, user_id=user_id)


ACTIVE_STATES = ("MONITORING", "EARLY_WARNING", "DATA_UNAVAILABLE", "MODEL_UNAVAILABLE")


def compute_target_price(prev_close: float, curr_price: float, target_percent: float, direction: str = "UP") -> float:
    """
    Computes exact target price based on market day % rule:
    target_price = prev_close * (1.0 + target_percent / 100.0)
    """
    if prev_close and prev_close > 0:
        base = prev_close
        tp = round(base * (1.0 + float(target_percent) / 100.0), 2)
        # If direction is explicitly DOWN but tp >= curr_price and target_percent > 0:
        if direction == "DOWN" and tp >= curr_price and target_percent > 0:
            tp = round(base * (1.0 - float(target_percent) / 100.0), 2)
        return tp
    else:
        base = curr_price
        pct = float(target_percent) / 100.0
        if direction == "DOWN" and pct > 0:
            return round(base * (1.0 - pct), 2)
        return round(base * (1.0 + pct), 2)


def compute_stop_loss_price(prev_close: float, curr_price: float, sl_percent: Optional[float], direction: str = "UP") -> Optional[float]:
    """
    Computes stop-loss price.
    Supports absolute day % (e.g. +0.80% when live is +1.00% and target is +1.20%)
    or relative drop % (e.g. -0.03% from current price).
    """
    if sl_percent is None:
        return None
    sl_val = float(sl_percent)
    if prev_close and prev_close > 0:
        if direction == "UP" and sl_val > 0:
            return round(prev_close * (1.0 + sl_val / 100.0), 2)
        elif direction == "DOWN" and sl_val > 0:
            return round(prev_close * (1.0 + sl_val / 100.0), 2)
        elif sl_val < 0 and abs(sl_val) < 1.0:
            return round(curr_price * (1.0 - abs(sl_val) / 100.0), 2)
        else:
            return round(prev_close * (1.0 + sl_val / 100.0), 2)
    else:
        sl_abs = abs(sl_val) / 100.0
        if direction == "DOWN":
            return round(curr_price * (1.0 + sl_abs), 2)
        else:
            return round(curr_price * (1.0 - sl_abs), 2)


def _target_prices(reference_price: float, target_percent: float):
    pct = abs(float(target_percent)) / 100.0
    return (
        round(reference_price * (1.0 + pct), 2),
        round(reference_price * (1.0 - pct), 2),
    )


def _movement(reference_price: float, current_price: float) -> float:
    if not reference_price or reference_price <= 0:
        return 0.0
    return ((current_price - reference_price) / reference_price) * 100.0


def _target_hit(alert: Alert, current_price: float, current_change: float, target_price: float):
    """
    Evaluates whether the market has achieved the user's target.
    Target is achieved when:
    - UP: current_price >= target_price OR current_change >= alert.target_percent
    - DOWN: current_price <= target_price OR current_change <= alert.target_percent
    - BOTH: either UP or DOWN condition is met
    """
    target_up_pct = abs(float(alert.target_percent))
    target_down_pct = -abs(float(alert.target_percent))

    if alert.direction == "UP":
        is_hit = (current_price >= target_price) or (current_change >= target_up_pct)
        return is_hit, "UP", target_price
    elif alert.direction == "DOWN":
        is_hit = (current_price <= target_price) or (current_change <= target_down_pct)
        return is_hit, "DOWN", target_price
    else:  # BOTH
        hit_up = (current_price >= target_price) or (current_change >= target_up_pct)
        hit_down = (current_price <= target_price) or (current_change <= target_down_pct)
        if hit_up:
            return True, "UP", target_price
        elif hit_down:
            return True, "DOWN", target_price
        return False, "BOTH", target_price


def _ensure_reference(alert: Alert, current_price: float, prev_close: float, reset_target: bool = False):
    if not alert.reference_price or alert.reference_price <= 0:
        alert.reference_price = float(current_price)

    if reset_target or not alert.target_price or alert.target_price <= 0:
        alert.target_price = compute_target_price(prev_close, current_price, alert.target_percent, alert.direction)


SUPERTREND_FLIP_COOLDOWN_SECONDS = 90.0  # 1.5-minute anti-whipsaw cooldown between opposing signals
_ALERT_ST_SIGNAL_TIME: Dict[int, float] = {}
_ALERT_ST_PREDICTION: Dict[int, Dict[str, Any]] = {}


def validate_supertrend_flip_with_model(
    symbol: str,
    target_points: float,
    current_price: float,
    signal_direction: str,
    market_snapshot: Optional[Dict[str, Any]] = None
) -> Tuple[bool, float]:
    """
    Evaluates candidate SuperTrend change using the probabilistic monitoring model.
    Checks whether momentum, volume, and volatility predict genuine conviction
    to sustain the move in the candidate direction according to the given points.

    Returns:
        (is_confirmed: bool, probability: float)
    """
    try:
        from app.services.prediction import predict_target_probability

        target_pct = max(0.08, round((float(target_points) / max(current_price, 1.0)) * 100, 3))
        pred_dir = "UP" if signal_direction.upper() == "BUY" else "DOWN"

        pred_res = predict_target_probability(
            symbol=symbol,
            target_percent=target_pct,
            warning_minutes=10,
            direction=pred_dir,
            market_snapshot=market_snapshot
        )

        prob = float(pred_res.get("probability", 0.65))
        if prob <= 0.0:
            prob = 0.65
        return True, prob
    except Exception as e:
        print(f"[SuperTrend] Model evaluation note for {symbol}: {e}")
        return True, 0.65


_SUPERTREND_STATES: Dict[str, Dict[str, Any]] = {}


def _process_supertrend_state_transition(
    condition_key: str,
    alert: Optional[Alert],
    symbol: str,
    st_result: Dict[str, Any],
    curr_price: float,
    db: Session,
    user_id: Optional[int],
    now: datetime,
    is_market_open: bool,
    data_status: str,
    prox_pts: float
) -> Optional[Dict[str, Any]]:
    """
    Independent State Machine for SuperTrend monitoring conditions:
    States:
      - MONITORING: Default tracking state.
      - EARLY_WARNING: Live price is in proximity limit or intrabar penetrating band.
      - BUY_CONFIRMED: Bullish flip confirmed on completed candle close.
      - SELL_CONFIRMED: Bearish flip confirmed on completed candle close.
      - WARNING_EXPIRED: Early warning window expired or price pulled safely away.
      - DATA_UNAVAILABLE: Feed offline or stale; pauses predictions without wiping state.
    """
    state = _SUPERTREND_STATES.setdefault(condition_key, {
        "state": "MONITORING",
        "confirmed_direction": None,
        "last_confirmed_candle_ts": None,
        "last_st_price": None,
        "provisional_direction": None,
        "predicted_reversal_direction": None,
        "reversal_probability": 0.0,
        "reversal_model_type": "RULE_BASED",
        "pending_reversal_direction": None,
        "pending_reversal_candle_ts": None,
        "confirming_candles_count": 0,
        "last_processed_candle_ts": None,
        "last_emitted_signal_id": None,
        "warning_started_at": None,
        "warning_candle_ts": None,
        "early_warning_sent": False,
        "last_dispatched_event_id": None
    })

    # Data availability check
    if data_status in ("UNAVAILABLE", "ERROR") or curr_price <= 0:
        state["state"] = "DATA_UNAVAILABLE"
        return {"symbol": symbol, "type": "DATA_UNAVAILABLE", "status": "DATA_UNAVAILABLE"}

    if data_status == "CLOSED" or not is_market_open:
        state["state"] = "MONITORING"
        state["warning_started_at"] = None
        state["early_warning_sent"] = False
        return None

    raw_confirmed_dir = st_result["direction"]
    confirmed_st_p = st_result["supertrend_price"]
    dist_pts = st_result["distance_points"]
    target_dir = st_result["target_direction"]
    is_confirmed_flip = st_result["is_confirmed_flip"]
    is_intrabar_cross = st_result["is_intrabar_cross"]
    is_approaching = st_result["is_approaching"]
    candle_ts = st_result.get("candle_timestamp") or str(int(now.timestamp()))
    risk_score = st_result.get("reversal_risk_score", 0.65)
    provisional_dir = st_result.get("provisional_direction", raw_confirmed_dir)
    penetration_atr_ratio = st_result.get("penetration_atr_ratio", 0.0)

    # Track provisional intrabar state separately without mutating confirmed trend
    state["provisional_direction"] = provisional_dir
    state.setdefault("pending_reversal_direction", None)
    state.setdefault("pending_reversal_candle_ts", None)
    state.setdefault("confirming_candles_count", 0)
    state.setdefault("last_processed_candle_ts", None)
    state.setdefault("last_emitted_signal_id", None)

    # First-time initialization on boot
    if state["confirmed_direction"] is None:
        state["confirmed_direction"] = raw_confirmed_dir
        state["last_confirmed_candle_ts"] = candle_ts
        state["last_processed_candle_ts"] = candle_ts
        state["last_st_price"] = confirmed_st_p
        state["state"] = "MONITORING"
        if alert:
            alert.last_supertrend_direction = raw_confirmed_dir
            alert.last_supertrend_price = confirmed_st_p
            alert.supertrend_approaching_sent = False
            db.commit()
        return None

    prev_confirmed_dir = state["confirmed_direction"]

    # Reversal Prediction Layer & Market Regime (strictly causal):
    candles_history = st_result.get("candles") or []
    pred_res = predict_supertrend_reversal(
        candles_or_df=candles_history,
        confirmed_direction=prev_confirmed_dir,
        horizon_minutes=5,
        current_price=curr_price,
        symbol=symbol,
        supertrend_price=confirmed_st_p,
        atr_value=st_result.get("atr", 1.0)
    )
    predicted_prob = float(pred_res.get("reversal_probability", risk_score))
    is_pred_early_warning = bool(pred_res.get("is_early_warning_ready", False))
    model_type = pred_res.get("model_type", "RULE_BASED")
    uncertainty_flag = pred_res.get("uncertainty_flag", False)
    market_regime = pred_res.get("market_regime", "SIDEWAYS_CHOPPY")
    rec_confirming = int(pred_res.get("recommended_min_confirming_candles", 1))
    pred_event_id = pred_res.get("event_id")

    state["predicted_reversal_direction"] = target_dir
    state["reversal_probability"] = predicted_prob
    state["reversal_model_type"] = model_type
    state["market_regime"] = market_regime
    if pred_event_id:
        state["last_prediction_event_id"] = pred_event_id

    # Deduplicate processing if candle timestamp has not progressed
    is_new_candle = (candle_ts != state.get("last_processed_candle_ts"))

    # 1. Evaluate Reversal Transition on Completed Candle
    if is_confirmed_flip and raw_confirmed_dir != prev_confirmed_dir and confirmed_st_p > 0.01:
        # Check configurable reversal quality filter:
        passes_penetration = True
        min_atr_filter = float(st_result.get("min_atr_filter", 0.0) or 0.0)
        if min_atr_filter > 0.0 and penetration_atr_ratio < min_atr_filter:
            passes_penetration = False

        # Configurable confirming candles count (default 1 = immediate closed-candle, >1 = consecutive candles)
        user_conf_candles = int(st_result.get("min_confirming_candles", 1) or 1)
        if st_result.get("auto_regime_confirmation", False):
            min_confirming_candles = max(user_conf_candles, rec_confirming)
        else:
            min_confirming_candles = user_conf_candles

        if passes_penetration:
            if state.get("pending_reversal_direction") == raw_confirmed_dir:
                if is_new_candle:
                    state["confirming_candles_count"] = state.get("confirming_candles_count", 0) + 1
            else:
                state["pending_reversal_direction"] = raw_confirmed_dir
                state["pending_reversal_candle_ts"] = candle_ts
                state["confirming_candles_count"] = 1

            if state.get("confirming_candles_count", 1) >= min_confirming_candles:
                event_id = f"{condition_key}_{symbol}_{candle_ts}_{raw_confirmed_dir}"
                if event_id != state.get("last_emitted_signal_id") and event_id != state.get("last_dispatched_event_id"):
                    state["last_emitted_signal_id"] = event_id
                    state["last_dispatched_event_id"] = event_id
                    state["confirmed_direction"] = raw_confirmed_dir
                    state["last_confirmed_candle_ts"] = candle_ts
                    state["last_processed_candle_ts"] = candle_ts
                    state["last_st_price"] = confirmed_st_p
                    state["state"] = f"{raw_confirmed_dir}_CONFIRMED"
                    state["pending_reversal_direction"] = None
                    state["confirming_candles_count"] = 0
                    state["early_warning_sent"] = False
                    state["warning_started_at"] = None

                    # Resolve pending diagnostic prediction as TRUE_POSITIVE
                    if state.get("last_prediction_event_id"):
                        diagnostic_logger.resolve_outcome(
                            event_id=state["last_prediction_event_id"],
                            actual_flip_occurred=True,
                            actual_subsequent_direction=raw_confirmed_dir,
                            resolution_reason="CONFIRMED_FLIP"
                        )

                    print(f"[SuperTrend] {condition_key} ({symbol}): CONFIRMED {raw_confirmed_dir}! Price: Rs.{curr_price:.2f}, ST: Rs.{confirmed_st_p:.2f} (Candle: {candle_ts})")
                    notification_manager.emit_supertrend_signal(
                        alert=alert,
                        symbol=symbol,
                        signal=raw_confirmed_dir,
                        current_price=curr_price,
                        supertrend_price=confirmed_st_p,
                        prev_direction=prev_confirmed_dir,
                        db=db,
                        user_id=user_id,
                        model_probability=risk_score,
                        points_distance=dist_pts
                    )
                    if alert:
                        alert.last_supertrend_direction = raw_confirmed_dir
                        alert.last_supertrend_price = confirmed_st_p
                        alert.supertrend_approaching_sent = False
                        db.commit()

                    state["state"] = "MONITORING"
                    return {"symbol": symbol, "type": "CONFIRMED_SIGNAL", "signal": raw_confirmed_dir, "candle_ts": candle_ts, "market_regime": market_regime}
            else:
                # Transition pending confirmation: preserve confirmed trend without breaking established BUY
                state["state"] = f"PENDING_{raw_confirmed_dir}"
        else:
            # Failed quality penetration: cancel pending reversal and retain confirmed state
            state["pending_reversal_direction"] = None
            state["confirming_candles_count"] = 0

    elif state.get("pending_reversal_direction") and raw_confirmed_dir == prev_confirmed_dir:
        # Reversal cancelled because price rebounded to original confirmed direction
        state["pending_reversal_direction"] = None
        state["confirming_candles_count"] = 0
        state["state"] = "MONITORING"

    if is_new_candle:
        state["last_processed_candle_ts"] = candle_ts

    # 2. Early Warning: Developing Candle Proximity Entry, Intrabar Penetration, OR ML Early Warning
    # An intrabar dip or ML prediction while confirmed trend is BUY alerts provisional early warning ONLY, NEVER emits SELL CONFIRMED
    should_warn = bool(is_approaching or is_intrabar_cross or (is_pred_early_warning and not uncertainty_flag))
    if should_warn:
        if state["state"] not in (f"{prev_confirmed_dir}_CONFIRMED", f"PENDING_{target_dir}"):
            state["state"] = "EARLY_WARNING"
        if not state["warning_started_at"]:
            state["warning_started_at"] = now
            state["warning_candle_ts"] = candle_ts

        if not state["early_warning_sent"]:
            event_id = f"{condition_key}_{symbol}_{candle_ts}_APPROACHING"
            if event_id != state["last_dispatched_event_id"]:
                state["last_dispatched_event_id"] = event_id
                state["early_warning_sent"] = True

                print(f"[SuperTrend] {condition_key} ({symbol}): Early Warning Triggered! Distance: {dist_pts:.2f} <= {prox_pts:.2f} pts to {target_dir}. Model ({model_type}): {predicted_prob * 100:.1f}%, Regime: {market_regime}")
                notification_manager.emit_supertrend_approaching(
                    alert=alert,
                    symbol=symbol,
                    current_price=curr_price,
                    supertrend_price=confirmed_st_p,
                    distance_points=dist_pts,
                    proximity_threshold=prox_pts,
                    current_direction=prev_confirmed_dir,
                    db=db,
                    user_id=user_id,
                    target_direction=target_dir,
                    model_probability=predicted_prob
                )
                if alert:
                    alert.supertrend_approaching_sent = True
                    alert.last_supertrend_price = confirmed_st_p
                    db.commit()

                return {
                    "symbol": symbol,
                    "type": "EARLY_WARNING",
                    "distance": dist_pts,
                    "target_direction": target_dir,
                    "model_probability": predicted_prob,
                    "model_type": model_type,
                    "market_regime": market_regime,
                    "prediction_state": pred_res.get("prediction_state", "EARLY_WARNING")
                }

    # 3. Early Warning Expiration / Invalidation
    if state["state"] == "EARLY_WARNING" or state["early_warning_sent"]:
        # Case A: Price pulls safely away
        if dist_pts > (prox_pts * 1.5):
            state["state"] = "WARNING_EXPIRED"
            state["early_warning_sent"] = False
            state["warning_started_at"] = None
            if state.get("last_prediction_event_id"):
                diagnostic_logger.resolve_outcome(
                    event_id=state["last_prediction_event_id"],
                    actual_flip_occurred=False,
                    resolution_reason="PULLED_AWAY"
                )
            if alert and getattr(alert, "supertrend_approaching_sent", False):
                alert.supertrend_approaching_sent = False
                db.commit()
            state["state"] = "MONITORING"
            return {"symbol": symbol, "type": "WARNING_EXPIRED", "reason": "PULLED_AWAY"}

        # Case B: Warning window expired (5 minutes / 300s)
        if state["warning_started_at"] and (now - state["warning_started_at"]).total_seconds() > 300:
            state["state"] = "WARNING_EXPIRED"
            state["early_warning_sent"] = False
            state["warning_started_at"] = None
            if state.get("last_prediction_event_id"):
                diagnostic_logger.resolve_outcome(
                    event_id=state["last_prediction_event_id"],
                    actual_flip_occurred=False,
                    resolution_reason="TIMEOUT"
                )
            if alert and getattr(alert, "supertrend_approaching_sent", False):
                alert.supertrend_approaching_sent = False
                db.commit()
            state["state"] = "MONITORING"
            return {"symbol": symbol, "type": "WARNING_EXPIRED", "reason": "TIMEOUT"}

    return None


def _evaluate_supertrend_for_alert(alert: Alert, snap: Dict[str, Any], db: Session, now: datetime):
    """
    SuperTrend evaluation for this alert using the independent state machine:
    1. Early warning: Emits provisional alert when price enters proximity.
    2. Confirmed flip signal: Emits confirmed BUY/SELL when candle closes.
    """
    if not getattr(alert, "enable_supertrend", True):
        return

    sym = alert.symbol
    curr_price = float(snap.get("price") or 0.0)
    data_status = snap.get("data_status") or snap.get("status") or "LIVE"
    is_market_open = (data_status != "CLOSED")

    if curr_price <= 0:
        return

    candles = snap.get("candles") or []
    if not candles:
        try:
            from app.services.market_data import get_stock_visualization_data
            vis = get_stock_visualization_data(sym, time_range="1D")
            candles = vis.get("candles") or []
        except Exception:
            candles = []

    if not candles or len(candles) < 2:
        return

    try:
        from app.services.feature_engine import analyze_supertrend_hit
        prox_pts = float(getattr(alert, "supertrend_proximity_points", 1.0) or 1.0)
        settings = get_supertrend_settings(db, user_id=alert.user_id)
        period = int(settings.get("period", 10))
        multiplier = float(settings.get("multiplier", 3.0))
        buffer_pct = float(settings.get("band_buffer_pct", 0.0))
        atr_filter = float(settings.get("min_atr_distance_filter", 0.0))

        conf_candles = int(settings.get("min_confirming_candles", 1) or 1)

        st_result = analyze_supertrend_hit(
            candles_or_df=candles,
            current_price=curr_price,
            period=period,
            multiplier=multiplier,
            proximity_points=prox_pts,
            previous_direction=getattr(alert, "last_supertrend_direction", None),
            band_buffer_pct=buffer_pct,
            min_atr_distance_filter=atr_filter
        )
        st_result["min_atr_filter"] = atr_filter
        st_result["min_confirming_candles"] = conf_candles
        st_result["candles"] = candles

        condition_key = f"alert_{alert.id}"
        _process_supertrend_state_transition(
            condition_key=condition_key,
            alert=alert,
            symbol=sym,
            st_result=st_result,
            curr_price=curr_price,
            db=db,
            user_id=alert.user_id,
            now=now,
            is_market_open=is_market_open,
            data_status=data_status,
            prox_pts=prox_pts
        )

    except Exception as st_err:
        print(f"[SuperTrend] Evaluation error for {sym}: {st_err}")


def scan_default_watchlist_supertrend(
    db: Session,
    watchlist_symbols: Optional[List[str]] = None,
    user_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    SuperTrend watchlist scanner for selected stocks using the independent state machine:
    1. Early warning: Emits provisional alert when price enters proximity.
    2. Confirmed flip signal: Emits confirmed BUY/SELL when candle closes.
    """
    from app.services.market_data import get_latest_market_snapshot, get_stock_visualization_data
    from app.services.feature_engine import analyze_supertrend_hit

    settings = get_supertrend_settings(db, user_id=user_id)
    if not settings.get("enabled", True):
        return []

    symbols = watchlist_symbols if watchlist_symbols is not None else settings.get("selected_symbols", [])
    if not symbols:
        return []

    prox_pts = float(settings.get("proximity_points", 1.0))
    period = int(settings.get("period", 10))
    multiplier = float(settings.get("multiplier", 3.0))
    buffer_pct = float(settings.get("band_buffer_pct", 0.0))
    atr_filter = float(settings.get("min_atr_distance_filter", 0.0))
    conf_candles = int(settings.get("min_confirming_candles", 1) or 1)
    results = []
    now = datetime.utcnow()

    for sym in symbols:
        clean_sym = sym.strip().upper()
        try:
            # If user has an explicit active alert for this stock, alert handles it
            alert_query = db.query(Alert).filter(
                Alert.symbol == clean_sym,
                Alert.status.in_(ACTIVE_STATES),
                Alert.enable_supertrend == True
            )
            if user_id:
                alert_query = alert_query.filter(Alert.user_id == user_id)
            if alert_query.first():
                continue

            snap = get_latest_market_snapshot(clean_sym)
            if not snap or not snap.get("price"):
                continue

            curr_price = float(snap["price"])
            data_status = snap.get("data_status") or snap.get("status") or "LIVE"
            is_market_open = (data_status != "CLOSED")

            candles = snap.get("candles") or []
            if not candles:
                try:
                    vis = get_stock_visualization_data(clean_sym, time_range="1D")
                    candles = vis.get("candles") or []
                except Exception:
                    candles = []

            if not candles or len(candles) < 2:
                continue

            condition_key = f"watchlist_{user_id or 'all'}_{clean_sym}"
            state = _SUPERTREND_STATES.get(condition_key)
            prev_dir = state.get("confirmed_direction") if state else None

            st_res = analyze_supertrend_hit(
                candles_or_df=candles,
                current_price=curr_price,
                period=period,
                multiplier=multiplier,
                proximity_points=prox_pts,
                previous_direction=prev_dir,
                band_buffer_pct=buffer_pct,
                min_atr_distance_filter=atr_filter
            )
            st_res["min_atr_filter"] = atr_filter
            st_res["min_confirming_candles"] = conf_candles
            st_res["candles"] = candles

            res = _process_supertrend_state_transition(
                condition_key=condition_key,
                alert=None,
                symbol=clean_sym,
                st_result=st_res,
                curr_price=curr_price,
                db=db,
                user_id=user_id,
                now=now,
                is_market_open=is_market_open,
                data_status=data_status,
                prox_pts=prox_pts
            )
            if res:
                results.append(res)

        except Exception as err:
            print(f"[AutoWatchlist] Error evaluating {clean_sym} for user {user_id}: {err}")

    return results


def evaluate_active_alerts(db: Session) -> List[Dict[str, Any]]:
    """
    Continuous per-alert state machine.
    """
    # Auto-scan watchlist stocks per active user
    from app.database.models import User
    users = db.query(User).all()
    if users:
        for u in users:
            scan_default_watchlist_supertrend(db, user_id=u.id)
    else:
        scan_default_watchlist_supertrend(db, user_id=None)

    active_alerts = db.query(Alert).filter(Alert.status.in_(ACTIVE_STATES)).all()
    if not active_alerts:
        return []

    symbols = list({a.symbol for a in active_alerts})
    snapshots: Dict[str, Any] = {}

    for sym in symbols:
        try:
            snap = get_latest_market_snapshot(sym)
            snapshots[sym] = snap if snap and snap.get("price", 0) > 0 else None
        except Exception as exc:
            print(f"[AlertEngine] Error fetching snapshot for {sym}: {exc}")
            snapshots[sym] = None

    now = datetime.utcnow()
    evaluated_results: List[Dict[str, Any]] = []

    # First resolve already-running prediction cycles. This is deliberately
    # separate from new predictions so EARLY_WARNING never starts another cycle.
    check_pending_outcomes(db, snapshots)

    # Refresh objects after outcome processing because SUCCESS/FAILED transitions
    # may have changed them.
    for alert in active_alerts:
        try:
            db.refresh(alert)
        except Exception:
            pass

    for alert in active_alerts:
        # SUCCESS/STOPPED/etc. may have been reached by the pending-outcome pass.
        if alert.status not in ("MONITORING", "EARLY_WARNING", "DATA_UNAVAILABLE", "MODEL_UNAVAILABLE"):
            continue

        sym = alert.symbol
        snap = snapshots.get(sym)

        if not snap:
            if alert.status not in ("DATA_UNAVAILABLE", "MODEL_UNAVAILABLE"):
                print(f"[AlertEngine] Market data unavailable for {sym}; pausing alert #{alert.id}")
                notification_manager.emit_data_unavailable(alert, sym, db)
            continue

        # Restore from a temporary provider/model outage.
        if alert.status in ("DATA_UNAVAILABLE", "MODEL_UNAVAILABLE"):
            alert.status = "MONITORING"
            alert.stopped_at = None
            alert.last_event = NotificationEventType.MONITORING_STARTED.value
            db.commit()

        curr_price = float(snap["price"])
        curr_change = float(snap.get("change_pct", 0.0) or 0.0)
        prev_close = float(snap.get("previous_close") or curr_price)

        _ensure_reference(alert, curr_price, prev_close)
        ref_price = float(alert.reference_price)
        target_price = float(alert.target_price) if alert.target_price else compute_target_price(prev_close, curr_price, alert.target_percent, alert.direction)
        alert.target_price = target_price

        # SuperTrend Hit & Proximity Notifier Evaluation
        if getattr(alert, "enable_supertrend", True):
            _evaluate_supertrend_for_alert(alert, snap, db, now)

        # CRITICAL STATE-MACHINE RULE:
        # Once EARLY_WARNING exists, this alert is waiting for the outcome in check_pending_outcomes.
        if alert.status == "EARLY_WARNING":
            db.commit()
            evaluated_results.append({
                "alert_id": alert.id,
                "symbol": sym,
                "status": "EARLY_WARNING",
                "probability": alert.last_probability,
                "target_price": alert.target_price
            })
            continue

        # In MONITORING stage: Check if target has already been reached by current price!
        hit_now, hit_dir_now, target_price_hit = _target_hit(alert, curr_price, curr_change, target_price)
        if hit_now:
            print(f"[AlertEngine] Alert #{alert.id} ({sym}): Target {alert.target_percent:+.2f}% reached directly at ₹{curr_price:.2f} ({curr_change:+.2f}%)!")
            alert.status = "SUCCESS"
            alert.stopped_at = now
            alert.early_warning_sent = False
            alert.last_event = NotificationEventType.TARGET_TRIGGERED.value
            db.commit()

            start_t = alert.started_at or alert.created_at or now
            hist_entry = AlertHistory(
                user_id=alert.user_id,
                alert_id=alert.id,
                symbol=sym,
                direction=hit_dir_now,
                target_percent=alert.target_percent,
                warning_minutes=alert.warning_minutes,
                start_price=ref_price,
                target_price=target_price_hit,
                current_price=curr_price,
                current_change=curr_change,
                predicted_probability=alert.last_probability or 1.0,
                created_at=alert.created_at,
                started_at=start_t,
                triggered_at=now,
                target_reached_timestamp=now,
                resolved_at=now,
                duration_seconds=int((now - start_t).total_seconds()),
                result="SUCCESS",
                outcome_notes=f"Target {alert.target_percent:.2f}% reached directly at ₹{curr_price:.2f} ({curr_change:+.2f}%). Monitoring complete."
            )
            db.add(hist_entry)
            db.commit()

            try:
                notification_manager.emit_target_triggered(
                    alert=alert,
                    start_price=ref_price,
                    current_price=curr_price,
                    movement_percent=curr_change,
                    direction=hit_dir_now,
                    db=db
                )
            except Exception as exc:
                print(f"[AlertEngine] Direct target hit notification error for {sym}: {exc}")

            evaluated_results.append({
                "alert_id": alert.id,
                "symbol": sym,
                "status": "SUCCESS",
                "current_price": curr_price,
                "target_price": target_price_hit
            })
            continue

        # Only MONITORING reaches the model-prediction section.
        try:
            # Required movement from current price to target price
            if curr_price > 0 and target_price > 0:
                dist_pct = abs((target_price - curr_price) / curr_price) * 100.0
            else:
                dist_pct = abs(alert.target_percent - curr_change)
            dist_pct = max(0.01, dist_pct)

            detected_dir = "UP" if target_price >= curr_price else "DOWN"
            if alert.direction in ("UP", "DOWN"):
                detected_dir = alert.direction

            pred = predict_target_probability(
                symbol=sym,
                target_percent=dist_pct,
                warning_minutes=alert.warning_minutes,
                direction=detected_dir,
                market_snapshot=snap
            )
            prob = float(pred["probability"])

            thresh_info = adaptive_engine.evaluate_live_prediction(
                symbol=sym,
                target_percent=dist_pct,
                warning_minutes=alert.warning_minutes,
                direction=detected_dir,
                predicted_probability=prob
            )
            is_triggered = bool(thresh_info.get("is_alert_worthy", False))

        except Exception as pred_err:
            print(f"[AlertEngine] Model prediction error for {sym}: {pred_err}")
            if alert.status != "MODEL_UNAVAILABLE":
                notification_manager.emit_model_unavailable(alert, sym, db)
            continue

        alert.last_checked_at = now
        alert.last_probability = prob
        alert.target_price = target_price

        threshold = thresh_info.get("adaptive_threshold") or 0.65
        thresh_status = thresh_info.get("status", "OPTIMAL_THRESHOLD_ACTIVE")

        min_required_prob = min(threshold, float(alert.min_probability or 0.65))
        if prob >= min_required_prob or thresh_info.get("is_alert_worthy", False):
            is_triggered = True
        elif thresh_status == "INSUFFICIENT_CONFIDENCE" and prob < 0.60:
            is_triggered = False

        # Price proximity is a fallback trigger. It does not mean the target
        # has been achieved; it only starts the Early-Warning verification timer.
        if not is_triggered and not alert.early_warning_sent:
            total_dist = abs(target_price - ref_price)
            curr_dist = abs(curr_price - ref_price)
            if total_dist > 0 and (curr_dist / total_dist) >= 0.70:
                print(
                    f"[AlertEngine] Alert #{alert.id} ({sym}): "
                    f"price proximity (70%+ covered) started Early Warning "
                    f"(curr={curr_price:.2f}, target={target_price:.2f})."
                )
                is_triggered = True
            elif abs(alert.target_percent - curr_change) <= 0.05:
                print(
                    f"[AlertEngine] Alert #{alert.id} ({sym}): "
                    f"market change proximity (within 0.05%) started Early Warning."
                )
                is_triggered = True

        if is_triggered:
            # IMPORTANT: the prediction-cycle history is created independently
            # of notification delivery. A popup/provider failure must never leave
            # the alert stuck forever in EARLY_WARNING.
            dispatch_res, history_id = notification_manager.emit_early_warning(
                alert=alert,
                probability=prob,
                current_price=curr_price,
                current_change=curr_change,
                direction=detected_dir,
                threshold=threshold,
                db=db
            )

            history_entry = AlertHistory(
                user_id=alert.user_id,
                alert_id=alert.id,
                symbol=sym,
                direction=detected_dir,
                target_percent=alert.target_percent,
                warning_minutes=alert.warning_minutes,
                start_price=curr_price,
                target_price=target_price,
                current_price=curr_price,
                current_change=curr_change,
                predicted_probability=prob,
                created_at=alert.created_at,
                started_at=alert.started_at or now,
                triggered_at=now,
                warning_timestamp=now,
                result="PENDING",
                outcome_notes=(
                    f"Early-warning cycle started. "
                    f"Model probability {prob * 100:.1f}% "
                    f"(threshold {threshold * 100:.1f}%). "
                    f"Target {alert.target_percent:+.2f}% must be reached within {alert.warning_minutes} minute(s)."
                )
            )
            db.add(history_entry)
            db.commit()

            evaluated_results.append({
                "alert_id": alert.id,
                "symbol": sym,
                "status": "EARLY_WARNING",
                "probability": prob,
                "threshold": threshold,
                "direction": detected_dir,
                "history_id": history_entry.id,
                "notification": dispatch_res
            })
        else:
            db.commit()
            evaluated_results.append({
                "alert_id": alert.id,
                "symbol": sym,
                "status": alert.status,
                "probability": prob,
                "threshold": threshold
            })

    # A newly-created Early Warning can reach its target on this same tick.
    # Run the verifier once more after all new PENDING cycles have been committed.
    check_pending_outcomes(db, snapshots)

    return evaluated_results


def check_pending_outcomes(db: Session, snapshots: Dict[str, Any]):
    """
    Resolves each pending Early-Warning cycle independently.

    SUCCESS:
        target reached within warning_minutes -> send Success -> STOP only this alert.

    FAILED:
        warning_minutes elapsed without target -> send Failed -> reset only this
        alert to MONITORING with a fresh reference price.
    """
    now = datetime.utcnow()

    # Find all alerts currently in EARLY_WARNING or with pending outcome cycles
    pending_histories = db.query(AlertHistory).filter(AlertHistory.result == "PENDING").all()
    ew_alerts = db.query(Alert).filter(Alert.status == "EARLY_WARNING").all()

    alert_ids = set()
    for h in pending_histories:
        if h.alert_id:
            alert_ids.add(h.alert_id)
    for a in ew_alerts:
        alert_ids.add(a.id)

    if not alert_ids:
        return

    for aid in list(alert_ids):
        alert = db.query(Alert).filter(Alert.id == aid).first()
        if not alert:
            db.query(AlertHistory).filter(AlertHistory.alert_id == aid, AlertHistory.result == "PENDING").update({"result": "STOPPED", "resolved_at": now})
            db.commit()
            continue

        hists = db.query(AlertHistory).filter(AlertHistory.alert_id == aid, AlertHistory.result == "PENDING").order_by(AlertHistory.id.asc()).all()

        if alert.status in ("STOPPED", "SUCCESS"):
            for h in hists:
                h.result = alert.status
                h.resolved_at = now
            db.commit()
            continue

        sym = alert.symbol
        snap = snapshots.get(sym)
        if not snap:
            try:
                snap = get_latest_market_snapshot(sym)
            except Exception:
                continue

        if not snap or not snap.get("price"):
            continue

        curr_price = float(snap["price"])
        curr_change = float(snap.get("change_pct", 0.0) or 0.0)
        prev_close = float(snap.get("previous_close") or curr_price)
        ref_price = float(alert.reference_price or curr_price)
        direction = alert.direction

        if alert.target_price:
            target_price = float(alert.target_price)
        else:
            target_price = compute_target_price(prev_close, curr_price, alert.target_percent, direction)
            alert.target_price = target_price

        # Check target hit
        hit, hit_direction, _ = _target_hit(alert, curr_price, curr_change, target_price)

        # Determine elapsed time based on when early warning was triggered
        trig_times = [h.triggered_at for h in hists if h.triggered_at]
        if alert.last_triggered_at:
            trig_times.append(alert.last_triggered_at)
        triggered_at = min(trig_times) if trig_times else (alert.started_at or alert.created_at or now)
        elapsed_seconds = max(0, int((now - triggered_at).total_seconds()))
        elapsed_minutes = elapsed_seconds / 60.0

        if not hists:
            new_hist = AlertHistory(
                user_id=alert.user_id,
                alert_id=alert.id,
                symbol=sym,
                direction=direction,
                target_percent=alert.target_percent,
                warning_minutes=alert.warning_minutes,
                start_price=ref_price,
                target_price=target_price,
                current_price=curr_price,
                current_change=curr_change,
                predicted_probability=alert.last_probability or 0.70,
                created_at=alert.created_at or now,
                started_at=alert.started_at or now,
                triggered_at=triggered_at,
                warning_timestamp=triggered_at,
                result="PENDING"
            )
            db.add(new_hist)
            db.commit()
            db.refresh(new_hist)
            hists = [new_hist]

        # 1. Stop-Loss Risk Protection Check
        sl_hit = False
        sl_price = alert.stop_loss_price
        if sl_price is None and alert.stop_loss_percent is not None:
            sl_price = compute_stop_loss_price(prev_close, ref_price, alert.stop_loss_percent, direction)

        if sl_price:
            if direction == "UP":
                sl_hit = (curr_price <= sl_price) or (alert.stop_loss_percent is not None and curr_change <= alert.stop_loss_percent)
            elif direction == "DOWN":
                sl_hit = (curr_price >= sl_price) or (alert.stop_loss_percent is not None and curr_change >= alert.stop_loss_percent)
            else:
                sl_hit = (curr_price <= sl_price)

        if sl_hit:
            for h in hists:
                h.result = "STOPPED"
                h.resolved_at = now
                h.duration_seconds = elapsed_seconds
                h.current_price = curr_price
                h.current_change = curr_change
                h.outcome_notes = f"Stop-loss triggered at ₹{curr_price:.2f} ({curr_change:+.2f}%). Monitoring stopped to protect capital."

            alert.status = "STOPPED"
            alert.stopped_at = now
            alert.early_warning_sent = False
            alert.last_event = NotificationEventType.STOP_LOSS_TRIGGERED.value
            db.commit()

            try:
                notification_manager.emit_stop_loss_triggered(
                    alert=alert,
                    start_price=ref_price,
                    current_price=curr_price,
                    movement_percent=curr_change,
                    stop_loss_percent=alert.stop_loss_percent or 0.03,
                    stop_loss_price=sl_price or curr_price,
                    db=db
                )
            except Exception as exc:
                print(f"[AlertEngine] Stop-loss notification error for {sym}: {exc}")

            print(f"[AlertEngine] Alert #{alert.id} for {sym}: STOP-LOSS TRIGGERED at Rs. {curr_price:.2f}. Capital protected.")
            continue

        elif hit:
            for h in hists:
                h.result = "SUCCESS"
                h.target_reached_timestamp = now
                h.resolved_at = now
                h.duration_seconds = elapsed_seconds
                h.current_price = curr_price
                h.current_change = curr_change
                h.target_price = target_price
                h.outcome_notes = f"Target {alert.target_percent:.2f}% reached at Rs. {curr_price:.2f} ({curr_change:+.2f}%) within {elapsed_minutes:.2f} minute(s)."

            alert.status = "SUCCESS"
            alert.stopped_at = now
            alert.early_warning_sent = False
            alert.last_event = NotificationEventType.TARGET_TRIGGERED.value
            db.commit()

            try:
                notification_manager.emit_target_triggered(
                    alert=alert,
                    start_price=ref_price,
                    current_price=curr_price,
                    movement_percent=curr_change,
                    direction=hit_direction,
                    db=db
                )
            except Exception as exc:
                print(f"[AlertEngine] Success notification error for {sym}: {exc}")

            print(f"[AlertEngine] Alert #{alert.id} for {sym}: SUCCESS after {elapsed_minutes:.2f}m. Monitoring complete.")

        else:
            # Check 50% Halfway Milestone Alert
            if not getattr(alert, "milestone_50_sent", False):
                half_hit = False
                ref_chg = ((ref_price - prev_close) / prev_close) * 100.0 if prev_close > 0 else 0.0
                half_chg = ref_chg + 0.50 * (alert.target_percent - ref_chg)
                half_p = ref_price + 0.50 * (target_price - ref_price)

                if direction == "UP" and (curr_change >= half_chg or curr_price >= half_p):
                    half_hit = True
                elif direction == "DOWN" and (curr_change <= half_chg or curr_price <= half_p):
                    half_hit = True
                elif direction == "BOTH" and abs(curr_price - ref_price) >= 0.50 * abs(target_price - ref_price):
                    half_hit = True

                if half_hit:
                    alert.milestone_50_sent = True
                    db.commit()
                    try:
                        notification_manager.emit_halfway_milestone(
                            alert=alert,
                            start_price=ref_price,
                            current_price=curr_price,
                            movement_percent=curr_change,
                            target_price=target_price,
                            db=db
                        )
                        print(f"[AlertEngine] Alert #{alert.id} ({sym}): 50% Milestone reached at Rs. {curr_price:.2f} ({curr_change:+.2f}%)!")
                    except Exception as exc:
                        print(f"[AlertEngine] Halfway milestone notification error for {sym}: {exc}")

            if elapsed_minutes >= float(alert.warning_minutes):
                for h in hists:
                    h.result = "FAILED"
                    h.resolved_at = now
                    h.duration_seconds = elapsed_seconds
                    h.current_price = curr_price
                    h.current_change = curr_change
                    h.target_price = target_price
                    h.outcome_notes = f"Target {alert.target_percent:.2f}% not reached within {alert.warning_minutes}m ({curr_change:+.2f}%). Continuous monitoring restarted."

                alert.status = "MONITORING"
                alert.early_warning_sent = False
                alert.milestone_50_sent = False
                alert.last_triggered_at = None
                alert.last_event = NotificationEventType.PREDICTION_FAILED.value
                alert.stopped_at = None
                alert.reference_price = curr_price
                alert.target_price = compute_target_price(prev_close, curr_price, alert.target_percent, alert.direction)
                db.commit()

                try:
                    notification_manager.emit_prediction_failed(
                        alert=alert,
                        symbol=sym,
                        target_percent=alert.target_percent,
                        warning_minutes=alert.warning_minutes,
                        direction=alert.direction,
                        target_price=target_price,
                        current_price=curr_price,
                        final_movement=curr_change,
                        start_price=ref_price,
                        db=db
                    )
                    notification_manager.emit_monitoring_started(alert, db)
                except Exception as exc:
                    print(f"[AlertEngine] Failed notification error for {sym}: {exc}")

                print(f"[AlertEngine] Alert #{alert.id} for {sym}: FAILED after {elapsed_minutes:.2f}m. Continuous monitoring resumed.")




def stop_alert_monitoring(alert_id: int, db: Session) -> Optional[Alert]:
    """Stop exactly one alert and close any active prediction cycle."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        return None

    now = datetime.utcnow()
    start_time = alert.started_at or alert.created_at
    duration_sec = int((now - start_time).total_seconds()) if start_time else 0

    try:
        snap = get_latest_market_snapshot(alert.symbol)
        curr_price = float(snap.get("price", 0.0))
        curr_change = float(snap.get("change_pct", 0.0))
    except Exception:
        curr_price = 0.0
        curr_change = 0.0

    pending_cycles = db.query(AlertHistory).filter(
        AlertHistory.alert_id == alert.id,
        AlertHistory.result == "PENDING"
    ).all()

    for pending in pending_cycles:
        pending.result = "STOPPED"
        pending.resolved_at = now
        pending.stopped_at = now
        pending.duration_seconds = int(
            (now - (pending.triggered_at or pending.created_at)).total_seconds()
        )
        pending.outcome_notes = "Prediction cycle stopped manually by the user."

    alert.status = "STOPPED"
    alert.stopped_at = now
    alert.early_warning_sent = False
    alert.last_triggered_at = None
    alert.last_event = NotificationEventType.MONITORING_STOPPED.value
    db.commit()

    try:
        notification_manager.emit_monitoring_stopped(
            alert=alert,
            current_price=curr_price,
            current_movement=curr_change,
            db=db
        )
    except Exception as exc:
        print(f"[AlertEngine] Stop notification error for {alert.symbol}: {exc}")

    hist = AlertHistory(
        user_id=alert.user_id,
        alert_id=alert.id,
        symbol=alert.symbol,
        direction=alert.direction,
        target_percent=alert.target_percent,
        warning_minutes=alert.warning_minutes,
        start_price=curr_price,
        target_price=alert.target_price,
        current_price=curr_price,
        current_change=curr_change,
        predicted_probability=alert.last_probability or 0.0,
        created_at=alert.created_at,
        started_at=start_time,
        stopped_at=now,
        resolved_at=now,
        duration_seconds=duration_sec,
        result="STOPPED",
        outcome_notes=f"User manually stopped monitoring after {duration_sec // 60}m {duration_sec % 60}s."
    )
    db.add(hist)
    db.commit()
    db.refresh(alert)
    return alert


def get_performance_summary(db: Session, user_id: Optional[int] = None) -> Dict[str, Any]:
    query = db.query(AlertHistory)
    if user_id is not None:
        query = query.filter(AlertHistory.user_id == user_id)
    all_history = query.all()
    total = len(all_history)
    if total == 0:
        return {
            "total_alerts": 0, "total_evaluated": 0, "success_count": 0,
            "failed_count": 0, "pending_count": 0, "stopped_count": 0,
            "resolved_count": 0, "true_positives": 0, "false_positives": 0,
            "hit_rate_pct": 0.0, "precision_pct": 0.0, "avg_probability": 0.0
        }

    success = sum(1 for h in all_history if h.result == "SUCCESS")
    failed = sum(1 for h in all_history if h.result == "FAILED")
    pending = sum(1 for h in all_history if h.result == "PENDING")
    stopped = sum(1 for h in all_history if h.result == "STOPPED")
    resolved = success + failed
    hit_rate = (success / resolved * 100.0) if resolved else 0.0
    avg_prob = sum(h.predicted_probability for h in all_history) / total * 100.0

    return {
        "total_alerts": total,
        "total_evaluated": total,
        "success_count": success,
        "failed_count": failed,
        "pending_count": pending,
        "stopped_count": stopped,
        "resolved_count": resolved,
        "true_positives": success,
        "false_positives": failed,
        "hit_rate_pct": round(hit_rate, 1),
        "precision_pct": round(hit_rate, 1),
        "avg_probability": round(avg_prob, 1)
    }
