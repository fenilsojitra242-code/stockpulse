import json
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from app.database.database import SessionLocal
from app.database.models import Notification, Alert
from app.services.messaging.events import NotificationEventType, NotificationPriority, get_default_priority
from app.services.messaging.message_builder import MessageBuilder
from app.services.messaging.providers.base import NotificationProvider
from app.services.messaging.providers.dashboard import DashboardNotifier
from app.services.messaging.providers.popup import PopupNotifier


class NotificationService:
    """
    Core Notification Engine for StockPulse:
    1. Manages in-app popup and dashboard notification providers.
    2. Builds formatted messages using MessageBuilder.
    3. Enforces limited retry strategy (up to 3 attempts with failure tracking).
    4. Records every notification event in the database.
    """

    def __init__(self):
        self._providers: Dict[str, NotificationProvider] = {}
        # Register in-app popup and live dashboard providers
        self.register_provider(PopupNotifier())
        self.register_provider(DashboardNotifier())

    def register_provider(self, provider: NotificationProvider) -> None:
        self._providers[provider.channel_name.lower()] = provider

    def get_provider(self, channel_name: str) -> Optional[NotificationProvider]:
        norm = channel_name.strip().lower()
        # Aliases
        if norm in ("browser", "in_app", "web", "system", "sound", "popup"):
            return self._providers.get("popup") or self._providers.get("dashboard")
        return self._providers.get(norm)


    def dispatch(
        self,
        event_type: str,
        data: Dict[str, Any],
        channels: Optional[List[str]] = None,
        db: Optional[Session] = None,
        max_retries: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Dispatches notification across requested channels.
        Always includes 'dashboard' unless explicitly excluded.
        """
        should_close_db = False
        if db is None:
            db = SessionLocal()
            should_close_db = True

        try:
            built_message = MessageBuilder.build(event_type, data)
            priority = data.get("priority") or get_default_priority(event_type)
            symbol = data.get("symbol", "N/A").upper()
            alert_id = data.get("alert_id")

            # Resolve channels: only dashboard and popup are supported
            resolved_channels = ["dashboard", "popup"]

            delivery_results = []
            now = datetime.utcnow()

            for chan in resolved_channels:
                provider = self.get_provider(chan)
                if not provider:
                    continue

                payload = {
                    **data,
                    "event_type": event_type,
                    "priority": priority,
                    "title": built_message["title"],
                    "plain_text": built_message["plain_text"],
                    "html": built_message["html"],
                    "short_summary": built_message["short_summary"],
                    "message": built_message["plain_text"],
                    "created_at": now.isoformat()
                }

                # Attempt delivery with limited retry strategy (Section 21)
                attempt = 0
                send_result = {"success": False, "error": "Not attempted"}

                while attempt < max_retries:
                    attempt += 1
                    try:
                        send_result = provider.send(payload)
                        if send_result.get("success"):
                            break
                        else:
                            # Limited delay before retry for external providers
                            if attempt < max_retries and chan != "dashboard":
                                time.sleep(0.5 * attempt)
                    except Exception as exc:
                        send_result = {"success": False, "error": str(exc)}
                        if attempt < max_retries and chan != "dashboard":
                            time.sleep(0.5 * attempt)

                status_str = "SENT" if send_result.get("success") else "FAILED"
                error_msg = send_result.get("error") if not send_result.get("success") else None

                delivery_results.append({
                    "channel": chan,
                    "status": status_str,
                    "attempts": attempt,
                    "error": error_msg,
                    "title": built_message["title"]
                })

            # Persist EXACTLY ONE notification event record to database (prevents duplicate popups & rows)
            notif_id = None
            try:
                primary_channel = "popup" if "popup" in resolved_channels else (resolved_channels[0] if resolved_channels else "dashboard")
                notif_record = Notification(
                    alert_id=alert_id,
                    symbol=symbol,
                    event_type=event_type,
                    priority=priority,
                    title=built_message["title"],
                    message=built_message["plain_text"],
                    channel=primary_channel,
                    status="SENT",
                    retry_count=0,
                    error=None,
                    created_at=now,
                    sent_at=now,
                    is_read=False,
                    extra_data=json.dumps({
                        "target_percent": data.get("target_percent"),
                        "warning_minutes": data.get("warning_minutes"),
                        "direction": data.get("direction"),
                        "probability": data.get("probability"),
                        "current_price": data.get("current_price"),
                        "start_price": data.get("start_price"),
                        "current_movement": data.get("current_movement") if data.get("current_movement") is not None else data.get("current_change"),
                        "current_change": data.get("current_change"),
                        "target_price": data.get("target_price"),
                        "short_summary": built_message["short_summary"],
                        "channels": resolved_channels
                    })
                )
                # Dashboard is the persistent in-app transport. Mark the
                # record SENT when at least one requested provider delivered it;
                # otherwise retain FAILED so transport problems are visible.
                any_success = any(r.get("status") == "SENT" for r in delivery_results)
                notif_record.status = "SENT" if any_success else "FAILED"
                notif_record.error = None if any_success else "; ".join(
                    str(r.get("error")) for r in delivery_results if r.get("error")
                ) or "No notification provider delivered the event."
                notif_record.retry_count = max(
                    [int(r.get("attempts", 0) or 0) for r in delivery_results] or [0]
                )
                db.add(notif_record)
                db.commit()
                db.refresh(notif_record)
                notif_id = notif_record.id
                for r in delivery_results:
                    r["notification_id"] = notif_id
            except Exception as db_err:
                print(f"[NotificationService] DB save error: {db_err}")

            return delivery_results

        finally:
            if should_close_db:
                db.close()


# Global Singleton
notification_service = NotificationService()
