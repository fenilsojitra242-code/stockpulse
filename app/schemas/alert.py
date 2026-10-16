from datetime import datetime
from typing import Optional, Literal, List
from pydantic import BaseModel, Field, field_validator


class AlertCreate(BaseModel):
    symbol: str = Field(..., description="Stock symbol, e.g., TCS, INFY, RELIANCE")
    direction: Literal["UP", "DOWN", "BOTH"] = Field("UP", description="Target direction: UP, DOWN, or BOTH")
    target_percent: float = Field(..., ge=-50.0, le=50.0, description="Target movement percentage, e.g. 1.0, 2.5, 5.0, or -1.0, -2.5")
    target_price: Optional[float] = Field(None, gt=0.0, description="Optional target price")
    stop_loss_percent: Optional[float] = Field(None, description="Optional stop-loss percentage movement, e.g. -0.03, -0.10")
    stop_loss_price: Optional[float] = Field(None, gt=0.0, description="Optional stop-loss price")
    warning_minutes: int = Field(..., ge=1, le=180, description="Warning time window in minutes, e.g. 2, 5, 10, 15, 30")
    min_probability: Optional[float] = Field(None, description="Automatically calculated by backend adaptive threshold system; users cannot manually override")
    monitoring_interval: Optional[int] = Field(1, ge=1, le=15, description="Monitoring scan interval in minutes (1, 2, 5)")
    notification_method: Optional[Literal["popup", "browser", "all"]] = Field("popup", description="In-app popup notification channel")
    recipient_email: Optional[str] = Field(None, description="Unused / Deprecated")
    cooldown_minutes: Optional[int] = Field(10, ge=1, le=120, description="Cooldown minutes between triggers")
    enable_supertrend: Optional[bool] = Field(True, description="Enable SuperTrend Hit Notifier")
    supertrend_proximity_percent: Optional[float] = Field(0.50, description="Proximity hit threshold in percent (0.25, 0.50, 0.75, 1.00)")
    supertrend_proximity_points: Optional[float] = Field(1.0, ge=0.05, le=100.0, description="Legacy proximity hit threshold in points")

    @field_validator("target_percent")
    @classmethod
    def clean_target_percent(cls, v: float) -> float:
        val = round(float(v), 2)
        if abs(val) < 0.01:
            raise ValueError("Target percentage cannot be 0. Please specify a non-zero movement (e.g. +1.0% or -1.0%).")
        return val

    @field_validator("symbol")
    @classmethod
    def clean_symbol(cls, v: str) -> str:
        s = v.strip().upper()
        if not s:
            raise ValueError("Stock symbol cannot be empty")
        return s


class AlertUpdate(BaseModel):
    direction: Optional[Literal["UP", "DOWN", "BOTH"]] = None
    target_percent: Optional[float] = Field(None, ge=-50.0, le=50.0)
    target_price: Optional[float] = Field(None, gt=0.0)
    stop_loss_percent: Optional[float] = None
    stop_loss_price: Optional[float] = None
    warning_minutes: Optional[int] = Field(None, ge=1, le=180)
    monitoring_interval: Optional[int] = Field(None, ge=1, le=15)
    notification_method: Optional[Literal["popup", "browser", "all"]] = None
    recipient_email: Optional[str] = None
    status: Optional[str] = None
    cooldown_minutes: Optional[int] = Field(None, ge=1, le=120)
    enable_supertrend: Optional[bool] = None
    supertrend_proximity_percent: Optional[float] = None
    supertrend_proximity_points: Optional[float] = None


class AlertResponse(BaseModel):
    id: int
    user_id: Optional[int]
    symbol: str
    direction: str
    target_percent: float
    reference_price: Optional[float] = None
    target_price: Optional[float] = None
    stop_loss_percent: Optional[float] = None
    stop_loss_price: Optional[float] = None
    milestone_50_sent: Optional[bool] = False
    warning_minutes: int
    min_probability: float
    adaptive_threshold: Optional[float] = None
    threshold_status: Optional[str] = None
    monitoring_interval: int
    notification_method: str
    recipient_email: Optional[str] = None
    status: str
    cooldown_minutes: int
    created_at: datetime
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    last_triggered_at: Optional[datetime] = None
    last_probability: Optional[float] = None

    # SuperTrend Notifier status
    enable_supertrend: Optional[bool] = True
    supertrend_proximity_percent: Optional[float] = 0.50
    supertrend_proximity_points: Optional[float] = 1.0
    last_supertrend_direction: Optional[str] = None
    last_supertrend_price: Optional[float] = None
    supertrend_price: Optional[float] = None
    supertrend_direction: Optional[str] = None
    supertrend_gap_percent: Optional[float] = None
    supertrend_gap_points: Optional[float] = None
    supertrend_is_approaching: Optional[bool] = None
    
    # Dynamic live fields populated on retrieval
    current_price: Optional[float] = None
    current_movement: Optional[float] = None
    remaining_percent: Optional[float] = None
    target_distance_rupees: Optional[float] = None
    progress_percent: Optional[float] = None

    class Config:
        from_attributes = True


class AlertHistoryResponse(BaseModel):
    id: int
    alert_id: Optional[int]
    symbol: str
    direction: str
    target_percent: float
    warning_minutes: int
    start_price: Optional[float] = None
    target_price: Optional[float] = None
    current_price: float
    current_change: float
    highest_movement: Optional[float] = None
    lowest_movement: Optional[float] = None
    predicted_probability: float
    triggered_at: datetime
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    result: str
    outcome_notes: Optional[str]

    class Config:
        from_attributes = True


class PredictRequest(BaseModel):
    symbol: str
    direction: Literal["UP", "DOWN", "BOTH"] = "UP"
    target_percent: float = Field(..., ge=-50.0, le=50.0)
    warning_minutes: int = Field(..., ge=1, le=180)

    @field_validator("target_percent")
    @classmethod
    def clean_target_percent(cls, v: float) -> float:
        val = round(float(v), 2)
        if abs(val) < 0.01:
            raise ValueError("Target percentage cannot be 0. Please specify a non-zero movement (e.g. +1.0% or -1.0%).")
        return val


class PredictResponse(BaseModel):
    symbol: str
    current_price: float
    current_change: float
    target_percent: float
    target_price: Optional[float] = None
    target_price_up: Optional[float] = None
    target_price_down: Optional[float] = None
    warning_minutes: int
    direction: str
    probability: float
    probability_up: Optional[float] = None
    probability_down: Optional[float] = None
    adaptive_threshold: Optional[float] = None
    threshold_status: Optional[str] = None
    features_summary: dict
    is_alert_worthy: bool
    prediction_label: str  # HIGH PROBABILITY, MODERATE, LOW, INSUFFICIENT CONFIDENCE
    status_label: str      # EARLY WARNING, MONITORING, INSUFFICIENT_CONFIDENCE
    explanation: str
    disclaimer: str
    feasibility: Optional[dict] = None


class TargetValidationRequest(BaseModel):
    symbol: str
    target_percent: float = Field(..., ge=-100.0, le=100.0)
    warning_minutes: Optional[int] = Field(5, ge=1, le=180)
    direction: Optional[Literal["UP", "DOWN", "BOTH"]] = "UP"

    @field_validator("target_percent")
    @classmethod
    def clean_target_percent(cls, v: float) -> float:
        val = round(float(v), 2)
        if abs(val) < 0.01:
            raise ValueError("Target percentage cannot be 0. Please specify a non-zero movement (e.g. +1.0% or -1.0%).")
        return val


class TargetValidationResponse(BaseModel):
    symbol: str
    company_name: Optional[str] = None
    current_price: float
    current_change: float
    target_percent: float
    target_price: float
    needed_distance_pct: float
    warning_minutes: int
    direction: str
    achievability_percent: int
    feasibility_status: str
    status_title: str
    status_color: str
    badge_text: str
    is_valid: bool
    requires_warning: bool
    historical_stats: dict
    recommended_target_percent: float
    recommended_target_price: float
    explanation: str


