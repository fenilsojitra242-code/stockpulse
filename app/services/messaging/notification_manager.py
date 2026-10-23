import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List, Set
from sqlalchemy.orm import Session

from app.database.models import Alert
from app.services.messaging.events import NotificationEventType
from app.services.messaging.notification_service import notification_service

# IST timezone (UTC+5:30)
_IST = timezone(timedelta(hours=5, minutes=30))


def _ist_now() -> datetime:
    """Returns current datetime in IST."""
    return datetime.now(_IST)


def _ist_str() -> str:
    """Returns current IST time as HH:MM IST string."""
    return datetime.now(_IST).strftime("%H:%M IST")



class NotificationManager:
    """
    State-Machine & Spam Protection Controller for StockPulse Notifications.
    Implements:
    - Pre-send validation (data freshness, monitoring active, model validity)
    - Anti-spam & duplicate suppression (EARLY_WARNING_SENT cycle lock)
    - Configurable cooldown enforcement (Section 15, 16, 26, 27)
    - Multi-session isolation (Section 19)
    """

    @staticmethod
    def _get_recipient_email(alert: Optional[Alert] = None) -> Optional[str]:
        return None

    @classmethod
    def _resolve_channels(cls, alert: Optional[Alert] = None) -> List[str]:
        return ["dashboard", "popup"]

    @classmethod
    def emit_monitoring_started(cls, alert: Alert, db: Session) -> List[Dict[str, Any]]:
        """Triggered when monitoring begins. Dispatches prediction active message to user configured channels."""
        alert.early_warning_sent = False
        alert.last_triggered_at = None
        alert.last_event = NotificationEventType.MONITORING_STARTED.value
        db.commit()

        channels = cls._resolve_channels(alert)

        data = {
            "alert_id": alert.id,
            "symbol": alert.symbol,
            "target_percent": alert.target_percent,
            "target_price": alert.target_price,
            "start_price": alert.reference_price,
            "current_price": alert.reference_price,
            "direction": alert.direction,
            "warning_minutes": alert.warning_minutes,
            "probability": getattr(alert, "last_probability", None) or 0.70,
            "recipient_email": cls._get_recipient_email(alert),
            "status": "Monitoring",
            "timestamp": _ist_str()
        }

        return notification_service.dispatch(
            event_type=NotificationEventType.MONITORING_STARTED.value,
            data=data,
            channels=channels,
            db=db
        )

    @classmethod
    def emit_early_warning(
        cls,
        alert: Alert,
        probability: float,
        current_price: float,
        current_change: float,
        direction: str,
        threshold: float,
        db: Session
    ) -> tuple[List[Dict[str, Any]], Optional[int]]:
        """
        Starts exactly one Early-Warning cycle.

        The state transition is committed before notification delivery. This is
        intentional: notification delivery is a side effect and must never be
        allowed to strand an alert in EARLY_WARNING without a PENDING outcome
        record.

        Returns:
            (delivery_results, None)
        """
        now = datetime.utcnow()

        # 1. Active status check
        if alert.status not in ("MONITORING", "EARLY_WARNING"):
            print(f"[NotificationManager] Suppressing alert #{alert.id}: monitoring not active (status={alert.status})")
            return [], None

        # 2. Duplicate suppression for current cycle (Section 15)
        if alert.early_warning_sent:
            print(f"[NotificationManager] Suppressing duplicate EARLY_WARNING for alert #{alert.id} ({alert.symbol}): already notified in this cycle.")
            return [], None

        # 3. Duplicate suppression: if already in early warning cycle, suppress
        if alert.status == "EARLY_WARNING" and alert.early_warning_sent:
            return [], None

        # State transition: Mark early_warning_sent
        alert.early_warning_sent = True
        alert.last_triggered_at = now
        alert.last_probability = probability
        alert.last_event = NotificationEventType.EARLY_WARNING.value
        alert.status = "EARLY_WARNING"
        db.commit()

        channels = cls._resolve_channels(alert)

        data = {
            "alert_id": alert.id,
            "symbol": alert.symbol,
            "target_percent": alert.target_percent,
            "target_price": alert.target_price,
            "warning_minutes": alert.warning_minutes,
            "direction": direction,
            "current_price": current_price,
            "start_price": alert.reference_price,
            "current_change": current_change,
            "current_movement": current_change,
            "probability": probability,
            "threshold": threshold,
            "recipient_email": cls._get_recipient_email(alert),
            "timestamp": now.strftime("%H:%M:%S")
        }

        try:
            delivery = notification_service.dispatch(
                event_type=NotificationEventType.EARLY_WARNING.value,
                data=data,
                channels=channels,
                db=db
            )
        except Exception as exc:
            print(f"[NotificationManager] EARLY_WARNING delivery error for #{alert.id}: {exc}")
            delivery = []

        return delivery, None

    @classmethod
    def emit_target_triggered(
        cls,
        alert: Alert,
        start_price: float,
        current_price: float,
        movement_percent: float,
        direction: str,
        db: Session
    ) -> Optional[List[Dict[str, Any]]]:
        """Triggered when price movement hits the configured target boundary."""

        now = datetime.utcnow()
        alert.last_event = NotificationEventType.TARGET_TRIGGERED.value
        db.commit()

        channels = cls._resolve_channels(alert)

        data = {
            "alert_id": alert.id,
            "symbol": alert.symbol,
            "target_percent": alert.target_percent,
            "target_price": alert.target_price,
            "warning_minutes": alert.warning_minutes,
            "direction": direction,
            "start_price": start_price,
            "current_price": current_price,
            "current_movement": movement_percent,
            "recipient_email": cls._get_recipient_email(alert),
            "timestamp": now.strftime("%H:%M:%S")
        }

        return notification_service.dispatch(
            event_type=NotificationEventType.TARGET_TRIGGERED.value,
            data=data,
            channels=channels,
            db=db
        )

    @classmethod
    def emit_prediction_failed(
        cls,
        alert: Optional[Alert],
        symbol: str,
        target_percent: float,
        warning_minutes: int,
        direction: str,
        target_price: float,
        current_price: float,
        final_movement: float,
        start_price: float,
        db: Session
    ) -> List[Dict[str, Any]]:
        """Triggered when warning window expires without hitting target. Delivered to configured channel."""
        now = datetime.utcnow()
        if alert:
            alert.last_event = NotificationEventType.PREDICTION_FAILED.value
            db.commit()

        channels = cls._resolve_channels(alert)

        data = {
            "alert_id": alert.id if alert else None,
            "symbol": symbol,
            "target_percent": target_percent,
            "target_price": target_price,
            "warning_minutes": warning_minutes,
            "direction": direction,
            "start_price": start_price,
            "current_price": current_price,
            "current_movement": final_movement,
            "recipient_email": cls._get_recipient_email(alert) if alert else None,
            "timestamp": now.strftime("%H:%M:%S")
        }

        return notification_service.dispatch(
            event_type=NotificationEventType.PREDICTION_FAILED.value,
            data=data,
            channels=channels,
            db=db
        )

    @classmethod
    def emit_stop_loss_triggered(
        cls,
        alert: Alert,
        start_price: float,
        current_price: float,
        movement_percent: float,
        stop_loss_percent: float,
        stop_loss_price: float,
        db: Session
    ) -> List[Dict[str, Any]]:
        """Triggered when price movement breaches stop-loss boundary, protecting capital."""
        now = datetime.utcnow()
        alert.status = "STOPPED"
        alert.stopped_at = now
        alert.last_event = NotificationEventType.STOP_LOSS_TRIGGERED.value
        db.commit()

        channels = cls._resolve_channels(alert)

        data = {
            "alert_id": alert.id,
            "symbol": alert.symbol,
            "target_percent": alert.target_percent,
            "target_price": alert.target_price,
            "warning_minutes": alert.warning_minutes,
            "direction": alert.direction,
            "start_price": start_price,
            "current_price": current_price,
            "current_movement": movement_percent,
            "stop_loss_percent": stop_loss_percent,
            "stop_loss_price": stop_loss_price,
            "recipient_email": cls._get_recipient_email(alert),
            "timestamp": now.strftime("%H:%M:%S")
        }

        return notification_service.dispatch(
            event_type=NotificationEventType.STOP_LOSS_TRIGGERED.value,
            data=data,
            channels=channels,
            db=db
        )

    @classmethod
    def emit_halfway_milestone(
        cls,
        alert: Alert,
        start_price: float,
        current_price: float,
        movement_percent: float,
        target_price: float,
        db: Session
    ) -> List[Dict[str, Any]]:
        """Triggered when 50% of the target distance is covered."""
        now = datetime.utcnow()
        alert.milestone_50_sent = True
        alert.last_event = NotificationEventType.HALFWAY_MILESTONE.value
        db.commit()

        channels = cls._resolve_channels(alert)

        data = {
            "alert_id": alert.id,
            "symbol": alert.symbol,
            "target_percent": alert.target_percent,
            "target_price": target_price,
            "warning_minutes": alert.warning_minutes,
            "direction": alert.direction,
            "start_price": start_price,
            "current_price": current_price,
            "current_movement": movement_percent,
            "recipient_email": cls._get_recipient_email(alert),
            "timestamp": now.strftime("%H:%M:%S")
        }

        return notification_service.dispatch(
            event_type=NotificationEventType.HALFWAY_MILESTONE.value,
            data=data,
            channels=channels,
            db=db
        )

    @classmethod
    def emit_monitoring_expired(
        cls,
        alert: Alert,
        current_price: float,
        final_movement: float,
        db: Session
    ) -> List[Dict[str, Any]]:
        """Triggered when continuous session elapses without reaching target."""
        now = datetime.utcnow()
        alert.status = "EXPIRED"
        alert.stopped_at = now
        alert.last_event = NotificationEventType.MONITORING_EXPIRED.value
        db.commit()

        channels = cls._resolve_channels(alert)

        data = {
            "alert_id": alert.id,
            "symbol": alert.symbol,
            "target_percent": alert.target_percent,
            "target_price": alert.target_price,
            "warning_minutes": alert.warning_minutes,
            "direction": alert.direction,
            "start_price": alert.reference_price,
            "current_price": current_price,
            "current_movement": final_movement,
            "recipient_email": cls._get_recipient_email(alert),
            "timestamp": now.strftime("%H:%M:%S")
        }

        return notification_service.dispatch(
            event_type=NotificationEventType.MONITORING_EXPIRED.value,
            data=data,
            channels=channels,
            db=db
        )

    @classmethod
    def emit_monitoring_stopped(
        cls,
        alert: Alert,
        current_price: float,
        current_movement: float,
        db: Session
    ) -> List[Dict[str, Any]]:
        """Triggered when user explicitly stops monitoring."""
        now = datetime.utcnow()
        alert.status = "STOPPED"
        alert.stopped_at = now
        alert.last_event = NotificationEventType.MONITORING_STOPPED.value
        db.commit()

        # Section 9: Do not send unnecessary external spam on user stop, record to dashboard
        channels = ["dashboard"]

        data = {
            "alert_id": alert.id,
            "symbol": alert.symbol,
            "target_percent": alert.target_percent,
            "warning_minutes": alert.warning_minutes,
            "direction": alert.direction,
            "current_price": current_price,
            "current_movement": current_movement,
            "timestamp": now.strftime("%H:%M:%S")
        }

        return notification_service.dispatch(
            event_type=NotificationEventType.MONITORING_STOPPED.value,
            data=data,
            channels=channels,
            db=db
        )

    @classmethod
    def emit_data_unavailable(cls, alert: Alert, symbol: str, db: Session) -> List[Dict[str, Any]]:
        """Triggered if market data provider fails."""
        alert.status = "DATA_UNAVAILABLE"
        alert.last_event = NotificationEventType.DATA_UNAVAILABLE.value
        db.commit()

        data = {
            "alert_id": alert.id,
            "symbol": symbol,
            "timestamp": _ist_str()
        }

        return notification_service.dispatch(
            event_type=NotificationEventType.DATA_UNAVAILABLE.value,
            data=data,
            channels=["dashboard"],
            db=db
        )

    @classmethod
    def emit_model_unavailable(cls, alert: Alert, symbol: str, db: Session) -> List[Dict[str, Any]]:
        """Triggered if ML prediction fails."""
        alert.status = "MODEL_UNAVAILABLE"
        alert.last_event = NotificationEventType.MODEL_UNAVAILABLE.value
        db.commit()

        data = {
            "alert_id": alert.id,
            "symbol": symbol,
            "timestamp": _ist_str()
        }

        return notification_service.dispatch(
            event_type=NotificationEventType.MODEL_UNAVAILABLE.value,
            data=data,
            channels=["dashboard"],
            db=db
        )

    _DEDUPLICATED_EVENT_KEYS: Set[str] = set()

    @classmethod
    def emit_supertrend_approaching(
        cls,
        alert: Optional[Alert],
        symbol: str,
        current_price: float,
        supertrend_price: float,
        distance_points: float,
        proximity_threshold: float,
        current_direction: str,
        db: Session,
        user_id: Optional[int] = None,
        target_direction: Optional[str] = None,
        model_probability: Optional[float] = None,
        horizon_minutes: int = 5,
        candle_ts: Optional[str] = None,
        timeframe: Optional[str] = "1m",
        prediction_cycle_id: Optional[str] = None,
        is_simulation: bool = False,
        distance_percent: Optional[float] = None,
        proximity_percent: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """Triggered when price is within user percentage/points AND model predicts SuperIndicator hit."""
        if supertrend_price <= 0.01 or current_price <= 0.01 or abs(supertrend_price - current_price) > (current_price * 0.25):
            print(f"[NotificationManager] Rejected invalid SuperTrend approaching alert for {symbol}: price={current_price}, ST={supertrend_price}")
            return []

        channels = cls._resolve_channels(alert)
        if alert and not is_simulation:
            alert.last_event = NotificationEventType.SUPERTREND_APPROACHING.value
            db.commit()
            if not user_id and getattr(alert, "user_id", None):
                user_id = alert.user_id

        resolved_target = target_direction or ("BUY" if current_direction.upper() == "SELL" else "SELL")

        # Stable, deterministic event key construction
        if is_simulation:
            event_key = f"sim:st_early:{user_id or 'all'}:{symbol.upper()}:{uuid.uuid4().hex[:8]}"
        else:
            event_key = f"st_early:{user_id or 'all'}:{symbol.upper()}:{timeframe or '1m'}:{candle_ts or 'latest'}:{current_direction.upper()}->{resolved_target}"
            if prediction_cycle_id:
                event_key += f":{prediction_cycle_id}"

            if event_key in cls._DEDUPLICATED_EVENT_KEYS:
                print(f"[NotificationManager] Suppressed duplicate SuperTrend approaching alert: {event_key}")
                return []
            if len(cls._DEDUPLICATED_EVENT_KEYS) >= 5000:
                cls._DEDUPLICATED_EVENT_KEYS = set(list(cls._DEDUPLICATED_EVENT_KEYS)[-2500:])
            cls._DEDUPLICATED_EVENT_KEYS.add(event_key)

        calc_dist_pct = distance_percent if distance_percent is not None else round((distance_points / max(0.01, current_price)) * 100.0, 4)
        calc_prox_pct = proximity_percent if proximity_percent is not None else 0.50

        data = {
            "user_id": user_id,
            "alert_id": alert.id if alert else None,
            "symbol": symbol.upper(),
            "current_price": current_price,
            "supertrend_price": supertrend_price,
            "distance_points": distance_points,
            "proximity_threshold": proximity_threshold,
            "distance_percent": calc_dist_pct,
            "proximity_percent": calc_prox_pct,
            "current_direction": current_direction,
            "target_direction": resolved_target,
            "direction": current_direction,
            "model_probability": model_probability,
            "horizon_minutes": horizon_minutes,
            "event_key": event_key,
            "prediction_cycle_id": prediction_cycle_id,
            "candle_timestamp": candle_ts,
            "is_simulation": is_simulation,
            "timestamp": _ist_str(),
            "extra_data": json.dumps({
                "symbol": symbol.upper(),
                "current_price": current_price,
                "supertrend_price": supertrend_price,
                "distance_points": distance_points,
                "proximity_threshold": proximity_threshold,
                "distance_percent": calc_dist_pct,
                "proximity_percent": calc_prox_pct,
                "current_direction": current_direction,
                "target_direction": resolved_target,
                "direction": current_direction,
                "model_probability": model_probability,
                "horizon_minutes": horizon_minutes,
                "event_key": event_key,
                "prediction_cycle_id": prediction_cycle_id,
                "candle_timestamp": candle_ts,
                "event": NotificationEventType.SUPERTREND_APPROACHING.value
            })
        }

        return notification_service.dispatch(
            event_type=NotificationEventType.SUPERTREND_APPROACHING.value,
            data=data,
            channels=channels,
            db=db
        )

    @classmethod
    def emit_supertrend_prediction_false(
        cls,
        alert: Optional[Alert],
        symbol: str,
        current_price: float,
        supertrend_price: float,
        current_direction: str,
        db: Session,
        user_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Triggered when early prediction fails to hit and price pulls away (Model prediction is FALSE)."""
        channels = cls._resolve_channels(alert)
        if alert:
            alert.last_event = NotificationEventType.SUPERTREND_FALSE.value
            db.commit()
            if not user_id and getattr(alert, "user_id", None):
                user_id = alert.user_id

        data = {
            "user_id": user_id,
            "alert_id": alert.id if alert else None,
            "symbol": symbol.upper(),
            "current_price": current_price,
            "supertrend_price": supertrend_price,
            "current_direction": current_direction,
            "direction": current_direction,
            "timestamp": _ist_str(),
            "extra_data": json.dumps({
                "symbol": symbol.upper(),
                "current_price": current_price,
                "supertrend_price": supertrend_price,
                "current_direction": current_direction,
                "direction": current_direction,
                "event": NotificationEventType.SUPERTREND_FALSE.value
            })
        }

        return notification_service.dispatch(
            event_type=NotificationEventType.SUPERTREND_FALSE.value,
            data=data,
            channels=channels,
            db=db
        )

    @classmethod
    def emit_supertrend_changed(
        cls,
        alert: Optional[Alert],
        symbol: str,
        current_price: float,
        supertrend_price: float,
        previous_color: str,
        current_color: str,
        previous_points: float,
        current_points: float,
        previous_direction: str,
        current_direction: str,
        change_reason: str,
        db: Session,
        user_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Triggered when forming candle color, size, or points change significantly after prediction."""
        channels = cls._resolve_channels(alert)
        if alert:
            alert.last_event = NotificationEventType.SUPERTREND_CHANGED.value
            db.commit()
            if not user_id and getattr(alert, "user_id", None):
                user_id = alert.user_id

        data = {
            "user_id": user_id,
            "alert_id": alert.id if alert else None,
            "symbol": symbol.upper(),
            "current_price": current_price,
            "supertrend_price": supertrend_price,
            "previous_color": str(previous_color).upper(),
            "current_color": str(current_color).upper(),
            "previous_points": previous_points,
            "current_points": current_points,
            "previous_direction": str(previous_direction).upper(),
            "current_direction": str(current_direction).upper(),
            "direction": str(current_direction).upper(),
            "change_reason": change_reason,
            "timestamp": _ist_str(),
            "extra_data": json.dumps({
                "symbol": symbol.upper(),
                "current_price": current_price,
                "supertrend_price": supertrend_price,
                "previous_color": str(previous_color).upper(),
                "current_color": str(current_color).upper(),
                "previous_points": previous_points,
                "current_points": current_points,
                "previous_direction": str(previous_direction).upper(),
                "current_direction": str(current_direction).upper(),
                "direction": str(current_direction).upper(),
                "change_reason": change_reason,
                "event": NotificationEventType.SUPERTREND_CHANGED.value
            })
        }

        return notification_service.dispatch(
            event_type=NotificationEventType.SUPERTREND_CHANGED.value,
            data=data,
            channels=channels,
            db=db
        )

    @classmethod
    def emit_supertrend_signal(
        cls,
        alert: Optional[Alert],
        symbol: str,
        signal: str,  # 'BUY' or 'SELL'
        current_price: float,
        supertrend_price: float,
        prev_direction: Optional[str],
        db: Session,
        user_id: Optional[int] = None,
        model_probability: Optional[float] = None,
        points_distance: Optional[float] = None,
        candle_ts: Optional[str] = None,
        timeframe: Optional[str] = "1m",
        prediction_cycle_id: Optional[str] = None,
        is_simulation: bool = False
    ) -> List[Dict[str, Any]]:
        """Triggered when SuperTrend flips direction (SuperIndicator Hit — Model Prediction is TRUE)."""
        if supertrend_price <= 0.01 or current_price <= 0.01 or abs(supertrend_price - current_price) > (current_price * 0.25):
            print(f"[NotificationManager] Suppressed invalid SuperTrend signal for {symbol}: price={current_price}, ST={supertrend_price}. Values outside physical limits.")
            return []

        channels = cls._resolve_channels(alert)
        is_buy = signal.upper() == "BUY"
        ev_type = NotificationEventType.SUPERTREND_BUY.value if is_buy else NotificationEventType.SUPERTREND_SELL.value

        if alert and not is_simulation:
            alert.last_event = ev_type
            db.commit()
            if not user_id and getattr(alert, "user_id", None):
                user_id = alert.user_id

        # Deterministic event key replaces 15-second cooldown
        if is_simulation:
            event_key = f"sim:st_signal:{user_id or 'all'}:{symbol.upper()}:{uuid.uuid4().hex[:8]}"
        else:
            event_key = f"st_signal:{user_id or 'all'}:{symbol.upper()}:{timeframe or '1m'}:{candle_ts or 'latest'}:{prev_direction or 'NONE'}->{signal.upper()}"
            if prediction_cycle_id:
                event_key += f":{prediction_cycle_id}"

            if event_key in cls._DEDUPLICATED_EVENT_KEYS:
                print(f"[NotificationManager] Suppressed duplicate SuperTrend signal by event_key: {event_key}")
                return []
            if len(cls._DEDUPLICATED_EVENT_KEYS) >= 5000:
                cls._DEDUPLICATED_EVENT_KEYS = set(list(cls._DEDUPLICATED_EVENT_KEYS)[-2500:])
            cls._DEDUPLICATED_EVENT_KEYS.add(event_key)

        data = {
            "user_id": user_id,
            "alert_id": alert.id if alert else None,
            "symbol": symbol.upper(),
            "current_price": current_price,
            "supertrend_price": supertrend_price,
            "signal": "BUY" if is_buy else "SELL",
            "direction": "BUY" if is_buy else "SELL",
            "prev_direction": prev_direction,
            "model_probability": model_probability,
            "points_distance": points_distance,
            "event_key": event_key,
            "prediction_cycle_id": prediction_cycle_id,
            "candle_timestamp": candle_ts,
            "is_simulation": is_simulation,
            "timestamp": _ist_str(),
            "extra_data": json.dumps({
                "symbol": symbol.upper(),
                "current_price": current_price,
                "supertrend_price": supertrend_price,
                "signal": "BUY" if is_buy else "SELL",
                "direction": "BUY" if is_buy else "SELL",
                "prev_direction": prev_direction,
                "model_probability": model_probability,
                "points_distance": points_distance,
                "event_key": event_key,
                "prediction_cycle_id": prediction_cycle_id,
                "candle_timestamp": candle_ts,
                "event": ev_type
            })
        }

        return notification_service.dispatch(
            event_type=ev_type,
            data=data,
            channels=channels,
            db=db
        )


notification_manager = NotificationManager()

