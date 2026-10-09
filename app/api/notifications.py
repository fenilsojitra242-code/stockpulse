from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database.database import get_db
from app.database.models import Notification, Alert
from app.schemas.notification import NotificationResponse, NotificationListResponse

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
    db: Session = Depends(get_db)
):
    """
    List historical and real-time notifications with optional filters.
    """
    query = db.query(Notification)

    if alert_id is not None:
        query = query.filter(Notification.alert_id == alert_id)
    if symbol:
        query = query.filter(Notification.symbol == symbol.strip().upper())
    if event_type:
        query = query.filter(Notification.event_type == event_type.strip().upper())
    if priority:
        query = query.filter(Notification.priority == priority.strip().upper())
    if channel:
        query = query.filter(Notification.channel == channel.strip().lower())
    if unread_only:
        query = query.filter(Notification.is_read == False)

    total = query.count()
    unread_count = db.query(Notification).filter(Notification.is_read == False).count()
    items = query.order_by(desc(Notification.created_at)).limit(limit).all()

    return {
        "total": total,
        "unread_count": unread_count,
        "notifications": items
    }


@router.get("/{notification_id}", response_model=NotificationResponse)
def get_notification(notification_id: int, db: Session = Depends(get_db)):
    """Fetch single notification details."""
    item = db.query(Notification).filter(Notification.id == notification_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Notification not found")
    return item


@router.post("/{notification_id}/read", response_model=NotificationResponse)
@router.patch("/{notification_id}/read", response_model=NotificationResponse)
def mark_notification_as_read(notification_id: int, db: Session = Depends(get_db)):
    """Mark a notification as read on the dashboard."""
    item = db.query(Notification).filter(Notification.id == notification_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Notification not found")
    item.is_read = True
    db.commit()
    db.refresh(item)
    return item


@router.post("/read-all")
def mark_all_notifications_read(db: Session = Depends(get_db)):
    """Mark all notifications as read."""
    count = db.query(Notification).filter(Notification.is_read == False).update({"is_read": True})
    db.commit()
    return {"message": "All notifications marked as read", "updated": count}


@router.post("/clear")
@router.delete("/clear")
@router.delete("")
def clear_all_notifications(db: Session = Depends(get_db)):
    """Clear all notifications from the feed."""
    count = db.query(Notification).delete(synchronize_session=False)
    db.commit()
    return {"message": "All notifications cleared successfully", "deleted": count}


@router.delete("/{notification_id}")
def delete_notification(notification_id: int, db: Session = Depends(get_db)):
    """Delete a single notification by ID."""
    item = db.query(Notification).filter(Notification.id == notification_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Notification not found")
    db.delete(item)
    db.commit()
    return {"message": "Notification deleted successfully", "id": notification_id}

