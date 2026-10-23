from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, field_serializer


class NotificationBase(BaseModel):
    alert_id: Optional[int] = None
    symbol: str
    event_type: str
    priority: str = "INFO"
    title: str
    message: str
    channel: str = "dashboard"
    status: str = "SENT"
    retry_count: int = 0
    error: Optional[str] = None
    is_read: bool = False
    event_key: Optional[str] = None
    extra_data: Optional[str] = None


class NotificationResponse(NotificationBase):
    id: int
    created_at: datetime
    sent_at: Optional[datetime] = None

    @field_serializer("created_at", "sent_at", check_fields=False)
    def serialize_dt(self, dt: Optional[datetime], _info):
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.isoformat() + "Z"
        return dt.isoformat()

    class Config:
        from_attributes = True


class NotificationFeedItem(BaseModel):
    id: int
    alert_id: Optional[int] = None
    symbol: str
    event_type: str
    priority: str
    title: str
    message: str
    channel: str
    status: str
    created_at: datetime
    is_read: bool


class NotificationListResponse(BaseModel):
    total: int
    unread_count: int
    notifications: List[NotificationResponse]
