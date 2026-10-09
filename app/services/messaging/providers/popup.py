from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
from app.services.messaging.providers.base import NotificationProvider

# IST timezone (UTC+5:30)
_IST = timezone(timedelta(hours=5, minutes=30))


def _ist_str() -> str:
    """Returns current IST time as HH:MM IST string."""
    return datetime.now(_IST).strftime("%H:%M IST")


# Centralized in-memory buffer for popup delivery events
_POPUP_NOTIF_FEED: List[Dict[str, Any]] = []
MAX_POPUP_FEED_CAPACITY = 100


def get_popup_feed() -> List[Dict[str, Any]]:
    return list(_POPUP_NOTIF_FEED)


def clear_popup_feed() -> None:
    global _POPUP_NOTIF_FEED
    _POPUP_NOTIF_FEED = []


class PopupNotifier(NotificationProvider):
    """
    Delivers customized real-time popup & sound alerts to the user's browser/system.
    Integrates directly with the Web Audio API chime engine and native desktop notifications.
    """

    @property
    def channel_name(self) -> str:
        return "popup"

    def send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        entry = {
            "id": payload.get("notification_id") or len(_POPUP_NOTIF_FEED) + 1,
            "alert_id": payload.get("alert_id"),
            "symbol": payload.get("symbol", "").upper(),
            "event_type": payload.get("event_type", "INFO"),
            "priority": payload.get("priority", "INFO"),
            "title": payload.get("title", "StockPulse Alert"),
            "short_summary": payload.get("short_summary", ""),
            "message": payload.get("plain_text") or payload.get("message", ""),
            "html": payload.get("html", ""),
            "created_at": payload.get("created_at") or datetime.utcnow().isoformat(),
            "timestamp": _ist_str(),
            "popup": True,
            "sound": True,
            "extra_data": payload.get("extra_data") or {
                "symbol": payload.get("symbol", "").upper(),
                "direction": payload.get("direction"),
                "target_percent": payload.get("target_percent"),
                "warning_minutes": payload.get("warning_minutes"),
                "current_price": payload.get("current_price"),
                "start_price": payload.get("start_price"),
                "current_movement": payload.get("current_movement") if payload.get("current_movement") is not None else payload.get("current_change"),
                "current_change": payload.get("current_change"),
                "target_price": payload.get("target_price"),
                "probability": payload.get("probability")
            }
        }

        _POPUP_NOTIF_FEED.insert(0, entry)
        if len(_POPUP_NOTIF_FEED) > MAX_POPUP_FEED_CAPACITY:
            _POPUP_NOTIF_FEED.pop()

        return {
            "success": True,
            "channel": self.channel_name,
            "message_id": str(entry["id"]),
            "error": None,
            "popup_entry": entry
        }
