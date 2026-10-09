from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List


class StockBase(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20, description="Stock ticker symbol (e.g. TCS, INFY, RELIANCE)")
    name: str = Field(..., min_length=1, max_length=150, description="Company name")
    exchange: Optional[str] = Field("NSE", description="Exchange code")
    is_active: Optional[bool] = True
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    market_cap: Optional[str] = None
    avg_volume: Optional[float] = None


class StockCreate(StockBase):
    pass


class StockResponse(StockBase):
    id: int

    class Config:
        from_attributes = True


class CandlePoint(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class StockDetailResponse(BaseModel):
    symbol: str
    name: str
    exchange: str
    price: float
    previous_close: float
    price_change: float
    change_pct: float
    day_high: float
    day_low: float
    open: float
    volume: float
    avg_volume: float
    market_cap: str
    fifty_two_week_high: float
    fifty_two_week_low: float
    market_status: str  # OPEN or CLOSED
    market_time: str
    last_updated: str
    features: Dict[str, Any]
    candles: List[CandlePoint]
