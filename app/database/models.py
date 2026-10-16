from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import relationship
from app.database.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, default="Trader")
    username = Column(String(50), unique=True, index=True, nullable=True)
    email = Column(String(150), unique=True, index=True, nullable=False, default="user@stockpulse.io")
    password_hash = Column(String(255), nullable=True)
    is_verified = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    alerts = relationship("Alert", back_populates="user", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")


class Stock(Base):
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(30), unique=True, index=True, nullable=False)
    name = Column(String(150), nullable=False)
    company_name = Column(String(150), nullable=True)
    exchange = Column(String(20), default="NSE")
    is_active = Column(Boolean, default=True)
    fifty_two_week_high = Column(Float, nullable=True)
    fifty_two_week_low = Column(Float, nullable=True)
    market_cap = Column(String(50), nullable=True)
    avg_volume = Column(Float, nullable=True)

    alerts = relationship("Alert", back_populates="stock")


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=True)
    symbol = Column(String(30), nullable=False, index=True)
    direction = Column(String(10), nullable=False, default="UP")  # UP, DOWN, BOTH
    target_percent = Column(Float, nullable=False)  # fully dynamic: e.g. 0.5, 2.5, 5.0
    reference_price = Column(Float, nullable=True)  # session starting price baseline (fixed for session)
    target_price = Column(Float, nullable=True)  # calculated target price based on reference_price
    warning_minutes = Column(Integer, nullable=False)  # prediction window in minutes: e.g. 5, 10, 15
    min_probability = Column(Float, default=0.70)  # probability threshold (backend controlled)
    monitoring_interval = Column(Integer, default=1)  # 1, 2, 5 minutes
    notification_method = Column(String(50), default="popup")  # popup
    recipient_email = Column(String(150), nullable=True)
    
    # State machine: MONITORING, EARLY_WARNING, SUCCESS, STOPPED, EXPIRED, DATA_UNAVAILABLE, MODEL_UNAVAILABLE
    status = Column(String(20), default="MONITORING", index=True)
    cooldown_minutes = Column(Integer, default=10)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, default=datetime.utcnow)
    stopped_at = Column(DateTime, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    last_triggered_at = Column(DateTime, nullable=True)
    last_probability = Column(Float, nullable=True)

    early_warning_sent = Column(Boolean, default=False)
    milestone_50_sent = Column(Boolean, default=False)
    stop_loss_percent = Column(Float, nullable=True)  # optional protective stop-loss % (e.g. 0.03)
    stop_loss_price = Column(Float, nullable=True)    # calculated stop-loss price
    last_event = Column(String(50), nullable=True)

    # SuperTrend Hit Notifier fields
    enable_supertrend = Column(Boolean, default=True)
    supertrend_proximity_percent = Column(Float, default=0.50)
    supertrend_proximity_points = Column(Float, default=1.0)
    last_supertrend_direction = Column(String(10), nullable=True)  # 'BUY' or 'SELL'
    last_supertrend_price = Column(Float, nullable=True)
    supertrend_approaching_sent = Column(Boolean, default=False)

    user = relationship("User", back_populates="alerts")
    stock = relationship("Stock", back_populates="alerts")
    history = relationship("AlertHistory", back_populates="alert", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="alert", cascade="all, delete-orphan")


class AlertHistory(Base):
    __tablename__ = "alert_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    alert_id = Column(Integer, ForeignKey("alerts.id", ondelete="CASCADE"), nullable=True)
    symbol = Column(String(30), nullable=False, index=True)
    direction = Column(String(10), nullable=False)
    target_percent = Column(Float, nullable=False)
    warning_minutes = Column(Integer, nullable=False)
    
    start_price = Column(Float, nullable=True)
    target_price = Column(Float, nullable=True)
    current_price = Column(Float, nullable=False)
    current_change = Column(Float, nullable=False)  # % movement at trigger/stop
    highest_movement = Column(Float, nullable=True)
    lowest_movement = Column(Float, nullable=True)
    predicted_probability = Column(Float, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    stopped_at = Column(DateTime, nullable=True)
    triggered_at = Column(DateTime, default=datetime.utcnow)
    warning_timestamp = Column(DateTime, nullable=True)
    target_reached_timestamp = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    
    # Outcome: PENDING, SUCCESS / Target reached, FAILED / Not reached, STOPPED, EXPIRED
    result = Column(String(30), default="PENDING", index=True)
    outcome_notes = Column(Text, nullable=True)

    alert = relationship("Alert", back_populates="history")
    user = relationship("User")


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String(50), primary_key=True)
    value = Column(Text, nullable=True)
    description = Column(String(255), nullable=True)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    alert_id = Column(Integer, ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True, index=True)
    symbol = Column(String(30), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, index=True)
    priority = Column(String(20), default="INFO", index=True)  # INFO, WARNING, IMPORTANT, ERROR
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    channel = Column(String(50), default="popup", index=True)  # popup, dashboard
    status = Column(String(30), default="SENT", index=True)  # SENT, FAILED, SUPPRESSED, PENDING
    retry_count = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    sent_at = Column(DateTime, nullable=True)
    is_read = Column(Boolean, default=False, index=True)
    event_key = Column(String(190), unique=True, index=True, nullable=True)
    extra_data = Column(Text, nullable=True)

    alert = relationship("Alert", back_populates="notifications")
    user = relationship("User", back_populates="notifications")


class EmailOTP(Base):
    __tablename__ = "email_otps"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(150), index=True, nullable=False)
    otp_code = Column(String(10), nullable=False)
    purpose = Column(String(30), default="REGISTRATION")
    expires_at = Column(DateTime, nullable=False)
    is_used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

