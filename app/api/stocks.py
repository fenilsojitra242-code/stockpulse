from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional

from app.database.database import get_db
from app.database.models import Stock, User
from app.schemas.stock import StockResponse, StockCreate
from app.api.auth import get_current_user
from app.services.market_data import (
    get_stock_visualization_data,
    get_available_stocks_list,
    get_clean_symbol,
    get_market_status,
    get_stock_data_status
)

router = APIRouter(prefix="/stocks", tags=["Stocks"])


@router.get("")
def list_available_stocks(response: Response):
    """List supported stocks for quick selection and search."""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return get_available_stocks_list()


@router.get("/search")
def search_stocks(q: str = Query("", description="Stock search query"), response: Response = None, db: Session = Depends(get_db)):
    """Search stocks by symbol or company name with universal candidate fallback."""
    if response:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    stocks = get_available_stocks_list()
    query = q.strip().upper()
    if not query:
        return stocks

    # 1. Match from curated master catalog
    matched = [s for s in stocks if query in s["symbol"].upper() or query in s["name"].upper()]
    matched_symbols = {s["symbol"].upper() for s in matched}

    # 2. Match from database
    try:
        db_stocks = db.query(Stock).filter(
            (Stock.symbol.ilike(f"%{query}%")) | (Stock.name.ilike(f"%{query}%"))
        ).limit(15).all()
        for ds in db_stocks:
            if ds.symbol.upper() not in matched_symbols:
                matched.append({
                    "symbol": ds.symbol,
                    "name": ds.name or ds.company_name or f"{ds.symbol} Equity",
                    "exchange": ds.exchange or "NSE"
                })
                matched_symbols.add(ds.symbol.upper())
    except Exception:
        pass

    # 3. Always guarantee query can be selected even if custom
    if query not in matched_symbols:
        custom_name = f"{query} Share / Equity"
        if query == "OLX":
            custom_name = "OLX India / CarTrade Tech Ltd"
        matched.insert(0, {
            "symbol": query,
            "name": custom_name,
            "exchange": "NSE"
        })

    return matched


@router.get("/{symbol}")
def get_stock_data(
    symbol: str,
    response: Response,
    range: str = Query("1D", pattern="^(1D|5D|1M|6M|1Y)$", description="Selectable time range: 1D, 5D, 1M, 6M, 1Y")
):
    """
    Get live stock header, price chart data for selected time range (1D, 5D, 1M, 6M, 1Y),
    and key market statistics.
    Returns status 'UNAVAILABLE' if data cannot be retrieved from provider.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    data = get_stock_visualization_data(symbol, time_range=range)
    return data


@router.get("/{symbol}/live")
def get_stock_fast_live(symbol: str, response: Response):
    """Lightweight live quote with separated market data timestamp and freshness status."""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    data = get_stock_visualization_data(symbol, time_range="1D")
    return {
        "symbol": data.get("symbol", symbol),
        "name": data.get("name", symbol),
        "price": data.get("price"),
        "change": data.get("price_change"),
        "change_percent": data.get("change_pct"),
        "previous_close": data.get("previous_close"),
        "price_change": data.get("price_change"),
        "change_pct": data.get("change_pct"),
        "volume": data.get("volume"),
        "latest_market_timestamp": data.get("latest_market_timestamp"),
        "data_received_at": data.get("data_received_at"),
        "data_interval": data.get("data_interval", "1m"),
        "data_status": data.get("data_status", "LIVE"),
        "data_age_seconds": data.get("data_age_seconds", 0),
        "last_updated": data.get("last_updated"),
        "market_status": data.get("market_status", "OPEN"),
        "fifty_two_week_high": data.get("fifty_two_week_high"),
        "fifty_two_week_low": data.get("fifty_two_week_low"),
        "provider": data.get("provider", "yfinance")
    }


@router.get("/{symbol}/data-status")
def get_stock_data_status_endpoint(symbol: str):
    """
    Diagnostic endpoint (Section 14).
    Exposes provider timestamp, server timestamp, data age, and status.
    """
    return get_stock_data_status(symbol)


@router.get("/{symbol}/technical")
def get_stock_technical_analysis(symbol: str):
    """Calculated technical indicators and quantitative signals for a stock."""
    from app.services.market_data import get_latest_market_snapshot, get_stock_visualization_data
    from app.services.prediction import predict_target_probability
    
    snapshot = get_latest_market_snapshot(symbol)
    vis = get_stock_visualization_data(symbol, time_range="1D")
    features = snapshot.get("features", {})
    
    price = vis.get("price", snapshot.get("price", 0.0))
    prev_close = vis.get("previous_close", snapshot.get("previous_close", price))
    
    rsi = round(features.get("rsi_14", 50.0), 2)
    macd = round(features.get("macd", 0.0), 3)
    macd_signal = round(features.get("macd_signal", 0.0), 3)
    macd_hist = round(features.get("macd_hist", 0.0), 3)
    
    sma_5 = round(features.get("sma_5", price), 2)
    sma_15 = round(features.get("sma_15", price), 2)
    sma_30 = round(features.get("sma_30", price), 2)
    ema_9 = round(features.get("ema_9", price), 2)
    ema_21 = round(features.get("ema_21", price), 2)
    
    atr = round(features.get("atr_14", 0.0), 2)
    natr = round(features.get("natr_14", 0.0), 2)
    vol_10 = round(features.get("volatility_10m", 0.0), 2)
    vol_30 = round(features.get("volatility_30m", 0.0), 2)
    
    roc_10 = round(features.get("roc_10m", 0.0), 2)
    ret_1m = round(features.get("return_1m", 0.0), 2)
    ret_5m = round(features.get("return_5m", 0.0), 2)
    
    bullish_pts = 0
    bearish_pts = 0
    if rsi > 55:
        bullish_pts += 1
    elif rsi < 45:
        bearish_pts += 1
        
    if macd > macd_signal:
        bullish_pts += 1
    else:
        bearish_pts += 1
        
    if price > sma_5:
        bullish_pts += 1
    else:
        bearish_pts += 1
        
    if price > ema_9:
        bullish_pts += 1
    else:
        bearish_pts += 1
        
    if bullish_pts >= 3:
        overall_sentiment = "BULLISH"
    elif bearish_pts >= 3:
        overall_sentiment = "BEARISH"
    else:
        overall_sentiment = "NEUTRAL"
        
    prediction = predict_target_probability(symbol, target_percent=2.5, warning_minutes=5, direction="UP")

    return {
        "symbol": vis.get("symbol", symbol),
        "name": vis.get("name", symbol),
        "price": price,
        "previous_close": prev_close,
        "price_change": vis.get("price_change", 0.0),
        "change_pct": vis.get("change_pct", 0.0),
        "rsi": rsi,
        "rsi_status": "Overbought" if rsi >= 70 else ("Oversold" if rsi <= 30 else "Neutral"),
        "macd": macd,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "sma_5": sma_5,
        "sma_15": sma_15,
        "sma_30": sma_30,
        "ema_9": ema_9,
        "ema_21": ema_21,
        "atr": atr,
        "natr": natr,
        "volatility_10m": vol_10,
        "volatility_30m": vol_30,
        "roc_10m": roc_10,
        "return_1m": ret_1m,
        "return_5m": ret_5m,
        "overall_sentiment": overall_sentiment,
        "bullish_points": bullish_pts,
        "bearish_points": bearish_pts,
        "prediction": prediction
    }


from pydantic import BaseModel


class SuperTrendSettingsPayload(BaseModel):
    enabled: Optional[bool] = None
    proximity_points: Optional[float] = None
    selected_symbols: Optional[List[str]] = None
    period: Optional[int] = None
    multiplier: Optional[float] = None
    band_buffer_pct: Optional[float] = None
    min_atr_distance_filter: Optional[float] = None
    min_confirming_candles: Optional[int] = None
    reversal_confidence_threshold: Optional[float] = None


@router.get("/supertrend/settings")
def get_supertrend_settings_endpoint(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Returns SuperTrend settings for the current user, including user-selected shares.
    """
    from app.services.alert_engine import get_supertrend_settings
    return get_supertrend_settings(db, user_id=current_user.id)


@router.post("/supertrend/settings")
def save_supertrend_settings_endpoint(
    payload: SuperTrendSettingsPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Saves SuperTrend settings for the current user:
    - enabled (bool)
    - proximity_points (float)
    - selected_symbols (list of stock symbols)
    - period (int, default 10)
    - multiplier (float, default 3.0)
    - band_buffer_pct (float, default 0.0)
    - min_atr_distance_filter (float, default 0.0)
    - min_confirming_candles (int, default 1)
    - reversal_confidence_threshold (float, default 0.50)
    """
    from app.services.alert_engine import save_supertrend_settings
    return save_supertrend_settings(
        db=db,
        enabled=payload.enabled,
        proximity_points=payload.proximity_points,
        selected_symbols=payload.selected_symbols,
        user_id=current_user.id,
        period=payload.period,
        multiplier=payload.multiplier,
        band_buffer_pct=payload.band_buffer_pct,
        min_atr_distance_filter=payload.min_atr_distance_filter,
        min_confirming_candles=payload.min_confirming_candles,
        reversal_confidence_threshold=payload.reversal_confidence_threshold
    )


@router.get("/{symbol}/supertrend")
def get_stock_supertrend(
    symbol: str,
    period: int = Query(10, ge=3, le=50, description="ATR period for SuperTrend"),
    multiplier: float = Query(3.0, ge=0.5, le=10.0, description="ATR multiplier"),
    proximity: float = Query(1.0, ge=0.1, le=50.0, description="Hit proximity threshold in points"),
    range: str = Query("1D", description="Time range for candles (1D, 5D, 1M, etc.)")
):
    """
    Returns calculated SuperTrend status and chart series for any stock.
    Indicates whether price is within proximity limit of hitting SuperTrend or flipped BUY/SELL.
    """
    from app.services.market_data import get_stock_visualization_data
    from app.services.feature_engine import analyze_supertrend_hit

    time_range_val = range.strip().upper() if range else "1D"
    vis = get_stock_visualization_data(symbol, time_range=time_range_val)

    # Use pre-computed supertrend from visualization data if standard parameters match
    if period == 10 and multiplier == 3.0 and proximity == 1.0 and vis.get("supertrend"):
        return vis["supertrend"]

    candles = vis.get("candles", [])
    curr_price = float(vis.get("price", 100.0))

    st_analysis = analyze_supertrend_hit(
        candles_or_df=candles,
        current_price=curr_price,
        period=period,
        multiplier=multiplier,
        proximity_points=proximity
    )

    return {
        "symbol": vis.get("symbol", symbol),
        "name": vis.get("name", symbol),
        "price": curr_price,
        "supertrend_price": st_analysis.get("supertrend_price", curr_price),
        "direction": st_analysis.get("direction", "BUY"),
        "previous_direction": st_analysis.get("previous_direction"),
        "target_direction": st_analysis.get("target_direction"),
        "distance_points": st_analysis.get("distance_points", 0.0),
        "proximity_threshold": st_analysis.get("proximity_threshold", proximity),
        "is_approaching": st_analysis.get("is_approaching", False),
        "is_prediction_ready": st_analysis.get("is_prediction_ready", False),
        "is_flipped": st_analysis.get("is_flipped", False),
        "flip_signal": st_analysis.get("flip_signal"),
        "candle_open": st_analysis.get("candle_open"),
        "candle_close": st_analysis.get("candle_close"),
        "candle_points": st_analysis.get("candle_points"),
        "candle_color": st_analysis.get("candle_color"),
        "candle_body_size": st_analysis.get("candle_body_size"),
        "is_candle_stable": st_analysis.get("is_candle_stable"),
        "candle_matches_target": st_analysis.get("candle_matches_target"),
        "candle_age_seconds": st_analysis.get("candle_age_seconds"),
        "series": st_analysis.get("series", [])
    }


@router.post("/{symbol}/supertrend/test-notify")
def test_supertrend_notification(
    symbol: str,
    signal_type: str = Query("APPROACHING", description="'APPROACHING', 'BUY', or 'SELL'"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Simulates / triggers a real-time SuperTrend notification for the stock
    so the user can immediately experience the Audio, Voice, and HUD popup alert.
    """
    from app.services.market_data import get_stock_visualization_data
    from app.services.messaging import notification_manager
    from app.database.models import Alert

    vis = get_stock_visualization_data(symbol, time_range="1D")
    curr_price = float(vis.get("price", 4120.0))
    clean_sym = vis.get("symbol", symbol).upper()

    # Isolate test notifications: do not query or mutate production alerts
    alert = None

    sig = signal_type.strip().upper()
    if sig == "BUY":
        st_price = round(curr_price - 1.20, 2)
        res = notification_manager.emit_supertrend_signal(
            alert=None,
            symbol=clean_sym,
            signal="BUY",
            current_price=curr_price,
            supertrend_price=st_price,
            prev_direction="SELL",
            db=db,
            user_id=current_user.id,
            model_probability=0.88,
            is_simulation=True
        )
    elif sig == "SELL":
        st_price = round(curr_price + 1.20, 2)
        res = notification_manager.emit_supertrend_signal(
            alert=None,
            symbol=clean_sym,
            signal="SELL",
            current_price=curr_price,
            supertrend_price=st_price,
            prev_direction="BUY",
            db=db,
            user_id=current_user.id,
            model_probability=0.84,
            is_simulation=True
        )
    elif sig == "FALSE":
        st_price = round(curr_price + 1.80, 2)
        res = notification_manager.emit_supertrend_prediction_false(
            alert=None,
            symbol=clean_sym,
            current_price=curr_price,
            supertrend_price=st_price,
            current_direction="SELL",
            db=db,
            user_id=current_user.id
        )
    elif sig == "CHANGED":
        st_price = round(curr_price + 1.10, 2)
        res = notification_manager.emit_supertrend_changed(
            alert=None,
            symbol=clean_sym,
            current_price=curr_price,
            supertrend_price=st_price,
            previous_color="GREEN",
            current_color="RED",
            previous_points=2.0,
            current_points=-2.0,
            previous_direction="BUY",
            current_direction="SELL",
            change_reason="Candle shifted from GREEN (+2.00 pts) to RED (-2.00 pts)",
            db=db,
            user_id=current_user.id
        )
    else:  # APPROACHING
        st_price = round(curr_price + 0.65, 2)
        res = notification_manager.emit_supertrend_approaching(
            alert=None,
            symbol=clean_sym,
            current_price=curr_price,
            supertrend_price=st_price,
            distance_points=0.65,
            proximity_threshold=1.0,
            current_direction="SELL",
            db=db,
            user_id=current_user.id,
            target_direction="BUY",
            model_probability=0.85,
            is_simulation=True
        )

    return {
        "status": "success",
        "symbol": clean_sym,
        "event_triggered": sig,
        "is_simulation": True,
        "results": res
    }


@router.get("/supertrend/diagnostics")
def get_supertrend_diagnostics(limit: int = 50, symbol: Optional[str] = None):
    """Retrieve recent causal prediction event logs for root-cause diagnosis."""
    from app.services.diagnostic_logger import diagnostic_logger
    logs = diagnostic_logger.get_recent_events(limit=limit, symbol=symbol)
    return {"status": "success", "count": len(logs), "logs": logs}


@router.get("/supertrend/diagnostics/summary")
def get_supertrend_diagnostics_summary():
    """Retrieve aggregate performance and false positive diagnostics across market regimes."""
    from app.services.diagnostic_logger import diagnostic_logger
    summary = diagnostic_logger.get_summary()
    return {"status": "success", "summary": summary}




def seed_default_stocks_if_needed(db: Session):
    """Seed initial stock list in DB and backfill any missing stocks."""
    try:
        default_stocks = get_available_stocks_list()
        existing_symbols = {s[0].upper() for s in db.query(Stock.symbol).all()}
        for s in default_stocks:
            sym = s["symbol"].upper()
            if sym not in existing_symbols:
                stk = Stock(
                    symbol=sym,
                    name=s["name"],
                    company_name=s["name"],
                    exchange=s.get("exchange", "NSE"),
                    is_active=True
                )
                db.add(stk)
                existing_symbols.add(sym)
        db.commit()
    except Exception as e:
        print(f"[StockPulse] Error seeding default stocks: {e}")
        db.rollback()

