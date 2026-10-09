from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
from app.services.messaging.providers.base import NotificationProvider

# IST timezone (UTC+5:30)
_IST = timezone(timedelta(hours=5, minutes=30))


def _ist_str() -> str:
    """Returns current IST time as HH:MM IST string."""
    return datetime.now(_IST).strftime("%H:%M IST")


# Centralized in-memory ring buffer for low-latency dashboard polling
_DASHBOARD_LIVE_FEED: List[Dict[str, Any]] = []
MAX_FEED_CAPACITY = 100


def get_dashboard_live_feed() -> List[Dict[str, Any]]:
    return list(_DASHBOARD_LIVE_FEED)


def clear_dashboard_live_feed() -> None:
    global _DASHBOARD_LIVE_FEED
    _DASHBOARD_LIVE_FEED = []


class DashboardNotifier(NotificationProvider):
    """
    Delivers notifications to the in-app Dashboard.
    Pushes to real-time ring buffer and persists for dashboard UI retrieval.
    """

    @property
    def channel_name(self) -> str:
        return "dashboard"

    def send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        entry = {
            "id": payload.get("notification_id") or len(_DASHBOARD_LIVE_FEED) + 1,
            "alert_id": payload.get("alert_id"),
            "symbol": payload.get("symbol", ""),
            "event_type": payload.get("event_type", "INFO"),
            "priority": payload.get("priority", "INFO"),
            "title": payload.get("title", ""),
            "short_summary": payload.get("short_summary", ""),
            "message": payload.get("plain_text") or payload.get("message", ""),
            "html": payload.get("html", ""),
            "created_at": payload.get("created_at") or datetime.utcnow().isoformat(),
            "timestamp": _ist_str(),
            "is_read": False,
            "extra_data": payload.get("extra_data")
        }

        _DASHBOARD_LIVE_FEED.insert(0, entry)
        if len(_DASHBOARD_LIVE_FEED) > MAX_FEED_CAPACITY:
            _DASHBOARD_LIVE_FEED.pop()

        return {
            "success": True,
            "channel": self.channel_name,
            "message_id": str(entry["id"]),
            "error": None,
            "feed_entry": entry
        }
