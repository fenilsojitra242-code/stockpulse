from enum import Enum
from typing import Dict


class NotificationEventType(str, Enum):
    MONITORING_STARTED = "MONITORING_STARTED"
    EARLY_WARNING = "EARLY_WARNING"
    HALFWAY_MILESTONE = "HALFWAY_MILESTONE"
    TARGET_TRIGGERED = "TARGET_TRIGGERED"
    STOP_LOSS_TRIGGERED = "STOP_LOSS_TRIGGERED"
    MONITORING_STOPPED = "MONITORING_STOPPED"
    MONITORING_EXPIRED = "MONITORING_EXPIRED"
    PREDICTION_FAILED = "PREDICTION_FAILED"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    NOTIFICATION_FAILED = "NOTIFICATION_FAILED"
    SUPERTREND_APPROACHING = "SUPERTREND_APPROACHING"
    SUPERTREND_BUY = "SUPERTREND_BUY"
    SUPERTREND_SELL = "SUPERTREND_SELL"
    SYSTEM_NOTIFICATION = "SYSTEM_NOTIFICATION"
    SYSTEM_STATUS = "SYSTEM_STATUS"


class NotificationPriority(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    IMPORTANT = "IMPORTANT"
    ERROR = "ERROR"


EVENT_PRIORITY_MAP: Dict[NotificationEventType, NotificationPriority] = {
    NotificationEventType.MONITORING_STARTED: NotificationPriority.INFO,
    NotificationEventType.EARLY_WARNING: NotificationPriority.WARNING,
    NotificationEventType.HALFWAY_MILESTONE: NotificationPriority.INFO,
    NotificationEventType.TARGET_TRIGGERED: NotificationPriority.IMPORTANT,
    NotificationEventType.STOP_LOSS_TRIGGERED: NotificationPriority.IMPORTANT,
    NotificationEventType.MONITORING_STOPPED: NotificationPriority.INFO,
    NotificationEventType.MONITORING_EXPIRED: NotificationPriority.WARNING,
    NotificationEventType.PREDICTION_FAILED: NotificationPriority.WARNING,
    NotificationEventType.DATA_UNAVAILABLE: NotificationPriority.ERROR,
    NotificationEventType.MODEL_UNAVAILABLE: NotificationPriority.ERROR,
    NotificationEventType.NOTIFICATION_FAILED: NotificationPriority.ERROR,
    NotificationEventType.SUPERTREND_APPROACHING: NotificationPriority.WARNING,
    NotificationEventType.SUPERTREND_BUY: NotificationPriority.IMPORTANT,
    NotificationEventType.SUPERTREND_SELL: NotificationPriority.IMPORTANT,
    NotificationEventType.SYSTEM_NOTIFICATION: NotificationPriority.INFO,
    NotificationEventType.SYSTEM_STATUS: NotificationPriority.INFO,
}


def get_default_priority(event_type: str) -> str:
    try:
        ev = NotificationEventType(event_type)
        return EVENT_PRIORITY_MAP.get(ev, NotificationPriority.INFO).value
    except Exception:
        return NotificationPriority.INFO.value
