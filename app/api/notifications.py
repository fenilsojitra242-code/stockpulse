from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_

from app.database.database import get_db
from app.database.models import Notification, Alert, User
from app.schemas.notification import NotificationResponse, NotificationListResponse
from app.api.auth import get_current_user

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    alert_id: Optional[int] = Query(None, description="Filter by alert ID"),
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    priority: Optional[str] = Query(None, description="Filter by priority (INFO, WARNING, IMPORTANT, ERROR)"),
    channel: Optional[str] = Query(None, description="Filter by channel"),
    unread_only: bool = Query(False, description="Filter unread only"),
    limit: int = Query(50, ge=1, le=200, description="Max notifications to return"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    List historical and real-time notifications for the authenticated user as well as system notifications.
    """
    # Include user's own notifications plus system notifications (user_id IS NULL)
    query = db.query(Notification).filter(
        or_(
            Notification.user_id == current_user.id,
            Notification.user_id.is_(None)
        )
    )

    if alert_id is not None and isinstance(alert_id, int):
        query = query.filter(Notification.alert_id == alert_id)
    if symbol and isinstance(symbol, str):
        query = query.filter(Notification.symbol == symbol.strip().upper())
    if event_type and isinstance(event_type, str):
        query = query.filter(Notification.event_type == event_type.strip().upper())
    if priority and isinstance(priority, str):
        query = query.filter(Notification.priority == priority.strip().upper())
    if channel and isinstance(channel, str):
        query = query.filter(Notification.channel == channel.strip().lower())
    if unread_only is True:
        query = query.filter(Notification.is_read == False)

    total = query.count()
    unread_count = db.query(Notification).filter(
        or_(
            Notification.user_id == current_user.id,
            Notification.user_id.is_(None)
        ),
        Notification.is_read == False
    ).count()
    items = query.order_by(desc(Notification.created_at)).limit(limit).all()

    return {
        "total": total,
        "unread_count": unread_count,
        "notifications": items
    }


@router.get("/{notification_id}", response_model=NotificationResponse)
def get_notification(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Fetch single notification details (user-isolated)."""
    item = db.query(Notification).filter(Notification.id == notification_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Notification not found")
    if item.user_id and item.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Unauthorized to access this notification")
    return item


@router.post("/{notification_id}/read", response_model=NotificationResponse)
@router.patch("/{notification_id}/read", response_model=NotificationResponse)
def mark_notification_as_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Mark a notification as read on the dashboard (user-isolated)."""
    item = db.query(Notification).filter(Notification.id == notification_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Notification not found")
    if item.user_id and item.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Unauthorized to modify this notification")
    item.is_read = True
    db.commit()
    db.refresh(item)
    return item


@router.post("/read-all")
def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Mark all notifications as read for current user and system notifications."""
    count = db.query(Notification).filter(
        or_(
            Notification.user_id == current_user.id,
            Notification.user_id.is_(None)
        ),
        Notification.is_read == False
    ).update({"is_read": True}, synchronize_session=False)
    db.commit()
    return {"message": "All notifications marked as read", "updated": count}


@router.post("/clear")
@router.delete("/clear")
@router.delete("")
def clear_all_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Clear all notifications belonging to current user and system notifications from the feed."""
    count = db.query(Notification).filter(
        or_(
            Notification.user_id == current_user.id,
            Notification.user_id.is_(None)
        )
    ).delete(synchronize_session=False)
    db.commit()
    return {"message": "All notifications cleared successfully", "deleted": count}


@router.post("/test-system")
def test_system_notification(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Dispatches a real-time system notification so the user can verify reception
    in the Notifications feed, In-App Popup HUD, Audio chime, and OS desktop notification.
    """
    from app.services.messaging import notification_service
    now = datetime.utcnow()
    res = notification_service.dispatch(
        event_type="SYSTEM_STATUS",
        data={
            "user_id": current_user.id,
            "symbol": "SYSTEM",
            "priority": "INFO",
            "direction": "ACTIVE",
            "current_price": 100.0,
            "target_percent": 0.0,
            "warning_minutes": 0,
            "probability": 1.0,
            "message": "StockPulse system monitoring engine is fully operational. Real-time telemetry, predictive barrier evaluation, and multi-channel notifications are running.",
            "title": "System Active: StockPulse Health Check",
            "short_summary": "StockPulse system monitoring and notification engine is active and operational.",
            "timestamp": now.strftime("%H:%M:%S")
        },
        channels=["dashboard", "popup"],
        db=db
    )
    return {"success": True, "message": "System notification dispatched successfully", "result": res}


@router.delete("/{notification_id}")
def delete_notification(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a single notification by ID (user-isolated)."""
    item = db.query(Notification).filter(Notification.id == notification_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Notification not found")
    if item.user_id and item.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Unauthorized to delete this notification")
    db.delete(item)
    db.commit()
    return {"message": "Notification deleted successfully", "id": notification_id}
