from datetime import datetime
from typing import Optional, Dict, Any, List

# In-memory log of recent notifications for the frontend dashboard live feed
_NOTIFICATION_FEED: List[Dict[str, Any]] = []
MAX_FEED_ITEMS = 50


def format_alert_message(
    symbol: str,
    current_change: float,
    current_price: float,
    target_percent: float,
    warning_minutes: int,
    probability: float,
    direction: str = "UP",
    custom_message: Optional[str] = None
) -> str:
    """Format alert message for in-app displays and popups."""
    sign = "+" if direction.upper() == "UP" else "-"
    change_sign = "+" if current_change >= 0 else ""
    prob_pct = int(probability * 100) if probability <= 1.0 else int(probability)
    
    msg_lines = [
        f"🚨 {symbol} EARLY MOVEMENT ALERT\n",
        f"Current movement: {change_sign}{current_change:.2f}%",
        f"Current price: ₹{current_price:.2f}",
        f"Target: {sign}{target_percent:.2f}%",
        f"Warning window: {warning_minutes} minutes",
        f"Model probability: {prob_pct}%\n",
        custom_message or f"{symbol} is showing short-term market behavior consistent with the configured target.",
        "\n⚠️ Model-generated estimate; not a guarantee."
    ]
    return "\n".join(msg_lines)


def dispatch_alert_notification(
    symbol: str,
    current_change: float,
    current_price: float,
    target_percent: float,
    warning_minutes: int,
    probability: float,
    direction: str = "UP"
) -> Dict[str, Any]:
    """
    Dispatches alert to in-app feed.
    """
    text = format_alert_message(
        symbol=symbol,
        current_change=current_change,
        current_price=current_price,
        target_percent=target_percent,
        warning_minutes=warning_minutes,
        probability=probability,
        direction=direction
    )
    
    feed_entry = {
        "id": len(_NOTIFICATION_FEED) + 1,
        "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
        "symbol": symbol,
        "current_change": current_change,
        "current_price": current_price,
        "target_percent": target_percent,
        "warning_minutes": warning_minutes,
        "probability": probability,
        "direction": direction,
        "formatted_text": text
    }
    _NOTIFICATION_FEED.insert(0, feed_entry)
    if len(_NOTIFICATION_FEED) > MAX_FEED_ITEMS:
        _NOTIFICATION_FEED.pop()
        
    return {
        "delivered": True,
        "feed_entry": feed_entry
    }


def get_notification_feed() -> List[Dict[str, Any]]:
    """Returns in-app live notifications feed."""
    return list(_NOTIFICATION_FEED)
