import time
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np
from typing import Dict, Optional, Tuple, List, Any

import threading

# Lazy loader for yfinance to defer heavy imports until required
_YF_MODULE = None

def _get_yf():
    """Lazily import and cache yfinance on first use."""
    global _YF_MODULE
    if _YF_MODULE is None:
        import yfinance as _yf
        _YF_MODULE = _yf
    return _YF_MODULE

def __getattr__(name: str):
    """Module-level attribute fallback for backward compatibility with tests/external code."""
    if name == "yf":
        return _get_yf()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# Indian Standard Time (IST) ZoneInfo
IST = ZoneInfo("Asia/Kolkata")

# In-memory cache: (clean_sym, time_range) -> {"data": dict, "timestamp": float}
_RANGE_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}
_FAST_INFO_CACHE: Dict[str, Dict[str, Any]] = {}
_SNAPSHOT_FEATURES_CACHE: Dict[Tuple, Dict[str, float]] = {}
_NEGATIVE_LOOKUP_CACHE: Dict[str, Any] = {}
_RESOLVED_TICKER_CACHE: Dict[str, str] = {}

CACHE_TTL_INTRADAY = 5      # seconds during open market (frequent fresh updates)
CACHE_TTL_HISTORICAL = 180  # 3 minutes for 5D, 1M, etc.
CACHE_TTL_CLOSED = 1800     # 30 minutes when market is closed (data doesn't change!)
FAST_INFO_CACHE_TTL = 7200  # 2 hours for static stats (market cap, 52w high/low)

# Configurable Yahoo Finance request timeouts (Phase 5 reliability)
YFINANCE_PRIMARY_TIMEOUT = float(os.getenv("YFINANCE_PRIMARY_TIMEOUT", "5.0"))
YFINANCE_SECONDARY_TIMEOUT = float(os.getenv("YFINANCE_SECONDARY_TIMEOUT", "2.5"))
YFINANCE_FALLBACK_TIMEOUT = float(os.getenv("YFINANCE_FALLBACK_TIMEOUT", "3.0"))

# Configurable Negative Cache TTLs (Phase 5 reliability)
NEGATIVE_CACHE_TTL_TRANSIENT = float(os.getenv("NEGATIVE_CACHE_TTL_TRANSIENT", "20.0"))
NEGATIVE_CACHE_TTL_INVALID = float(os.getenv("NEGATIVE_CACHE_TTL_INVALID", "300.0"))

# In-flight request deduplication to prevent redundant concurrent fetches
_IN_FLIGHT_LOCK = threading.Lock()

_IN_FLIGHT_EVENTS: Dict[Tuple[str, str], threading.Event] = {}

# Known company metadata for Indian & global stocks
STOCK_METADATA = {
    "TCS": {"name": "Tata Consultancy Services", "market_cap": "₹15.2 Lakh Cr", "52w_high": 4585.0, "52w_low": 3311.0, "avg_vol": "1.85M"},
    "INFY": {"name": "Infosys Ltd", "market_cap": "₹7.8 Lakh Cr", "52w_high": 1991.0, "52w_low": 1358.0, "avg_vol": "2.40M"},
    "RELIANCE": {"name": "Reliance Industries", "market_cap": "₹20.4 Lakh Cr", "52w_high": 3217.0, "52w_low": 2220.0, "avg_vol": "3.10M"},
    "HDFCBANK": {"name": "HDFC Bank Ltd", "market_cap": "₹12.6 Lakh Cr", "52w_high": 1794.0, "52w_low": 1363.0, "avg_vol": "4.20M"},
    "ICICIBANK": {"name": "ICICI Bank Ltd", "market_cap": "₹8.9 Lakh Cr", "52w_high": 1335.0, "52w_low": 912.0, "avg_vol": "3.50M"},
    "SBIN": {"name": "State Bank of India", "market_cap": "₹7.2 Lakh Cr", "52w_high": 912.0, "52w_low": 543.0, "avg_vol": "5.10M"},
    "BHARTIARTL": {"name": "Bharti Airtel Ltd", "market_cap": "₹9.8 Lakh Cr", "52w_high": 1779.0, "52w_low": 890.0, "avg_vol": "2.10M"},
    "WIPRO": {"name": "Wipro Ltd", "market_cap": "₹2.8 Lakh Cr", "52w_high": 580.0, "52w_low": 375.0, "avg_vol": "2.80M"},
    "OLX": {"name": "OLX India (CarTrade Tech Ltd)", "market_cap": "₹13.4K Cr", "52w_high": 3150.0, "52w_low": 1820.0, "avg_vol": "450K"},
    "CARTRADE": {"name": "CarTrade Tech Ltd (OLX India)", "market_cap": "₹13.4K Cr", "52w_high": 3150.0, "52w_low": 1820.0, "avg_vol": "450K"},
    "ZOMATO": {"name": "Zomato Ltd", "market_cap": "₹2.4 Lakh Cr", "52w_high": 298.0, "52w_low": 112.0, "avg_vol": "14.5M"},
    "PAYTM": {"name": "One97 Communications Ltd (Paytm)", "market_cap": "₹52.0K Cr", "52w_high": 998.0, "52w_low": 310.0, "avg_vol": "6.2M"},
    "SWIGGY": {"name": "Swiggy Ltd", "market_cap": "₹1.1 Lakh Cr", "52w_high": 580.0, "52w_low": 390.0, "avg_vol": "8.5M"},
    "NYKAA": {"name": "FSN E-Commerce Ventures (Nykaa)", "market_cap": "₹51.2K Cr", "52w_high": 228.0, "52w_low": 139.0, "avg_vol": "3.8M"},
    "POLICYBZR": {"name": "PB Fintech Ltd (PolicyBazaar)", "market_cap": "₹82.5K Cr", "52w_high": 1945.0, "52w_low": 980.0, "avg_vol": "2.1M"},
    "TATAMOTORS": {"name": "Tata Motors Ltd", "market_cap": "₹3.8 Lakh Cr", "52w_high": 1179.0, "52w_low": 610.0, "avg_vol": "7.8M"},
    "TATASTEEL": {"name": "Tata Steel Ltd", "market_cap": "₹1.9 Lakh Cr", "52w_high": 184.0, "52w_low": 115.0, "avg_vol": "18.2M"},
    "ITC": {"name": "ITC Ltd", "market_cap": "₹6.1 Lakh Cr", "52w_high": 528.0, "52w_low": 399.0, "avg_vol": "9.4M"},
    "HINDUNILVR": {"name": "Hindustan Unilever Ltd", "market_cap": "₹6.8 Lakh Cr", "52w_high": 3034.0, "52w_low": 2170.0, "avg_vol": "1.2M"},
    "LT": {"name": "Larsen & Toubro Ltd", "market_cap": "₹5.1 Lakh Cr", "52w_high": 3919.0, "52w_low": 2870.0, "avg_vol": "1.6M"},
    "AAPL": {"name": "Apple Inc.", "market_cap": "$3.4 Trillion", "52w_high": 237.23, "52w_low": 164.08, "avg_vol": "48.2M"},
    "MSFT": {"name": "Microsoft Corporation", "market_cap": "$3.2 Trillion", "52w_high": 468.35, "52w_low": 309.45, "avg_vol": "21.5M"},
    "NVDA": {"name": "NVIDIA Corporation", "market_cap": "$3.1 Trillion", "52w_high": 140.76, "52w_low": 39.23, "avg_vol": "54.8M"}
}

TICKER_ALIASES = {
    "OLX": "CARTRADE.NS",
    "CARTRADE": "CARTRADE.NS",
    "ZOMATO": "ZOMATO.NS",
    "SWIGGY": "SWIGGY.NS",
    "PAYTM": "PAYTM.NS",
    "ONE97": "PAYTM.NS",
    "NYKAA": "NYKAA.NS",
    "POLICYBZR": "POLICYBZR.NS",
    "DMART": "DMART.NS"
}


def normalize_ticker(symbol: str) -> str:
    """Normalize ticker symbol for Yahoo Finance."""
    s = symbol.strip().upper()
    if s in TICKER_ALIASES:
        return TICKER_ALIASES[s]
    if "." in s or "^" in s:
        return s
    
    us_tickers = {"AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "SPY", "QQQ", "PRX", "PROSY"}
    if s in us_tickers:
        return s
    
    # Default to National Stock Exchange (NSE) of India
    return f"{s}.NS"


def get_clean_symbol(ticker: str) -> str:
    """Strip exchange suffix for display."""
    return ticker.replace(".NS", "").replace(".BO", "")


def format_volume_metric(vol: float) -> str:
    """Format numeric volume into compact readable string (e.g. 2.4M, 850K)."""
    if not vol or vol <= 0:
        return "N/A"
    if vol >= 1_000_000_000:
        return f"{vol / 1_000_000_000:.2f}B"
    if vol >= 1_000_000:
        return f"{vol / 1_000_000:.2f}M"
    if vol >= 1_000:
        return f"{vol / 1_000:.1f}K"
    return f"{int(vol):,}"


# Official NSE / BSE Trading Holidays (2025 - 2026)
NSE_HOLIDAYS = {
    # 2025
    "2025-01-26",  # Republic Day
    "2025-02-26",  # Mahashivratri
    "2025-03-14",  # Holi
    "2025-03-31",  # Id-Ul-Fitr
    "2025-04-10",  # Shri Mahavir Jayanti
    "2025-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2025-04-18",  # Good Friday
    "2025-05-01",  # Maharashtra Day
    "2025-06-07",  # Bakri Id
    "2025-07-06",  # Muharram
    "2025-08-15",  # Independence Day
    "2025-08-27",  # Ganesh Chaturthi
    "2025-10-02",  # Mahatma Gandhi Jayanti
    "2025-10-21",  # Diwali Balipratipada
    "2025-11-05",  # Guru Nanak Jayanti
    "2025-12-25",  # Christmas
    # 2026
    "2026-01-26",  # Republic Day
    "2026-02-16",  # Mahashivratri
    "2026-03-04",  # Holi
    "2026-03-21",  # Id-Ul-Fitr
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-05-28",  # Bakri Id
    "2026-06-26",  # Muharram
    "2026-08-15",  # Independence Day
    "2026-09-14",  # Ganesh Chaturthi
    "2026-10-02",  # Mahatma Gandhi Jayanti
    "2026-10-20",  # Dussehra
    "2026-11-10",  # Diwali Balipratipada
    "2026-11-24",  # Guru Nanak Jayanti
    "2026-12-25",  # Christmas
}


# US Eastern Time (ET) ZoneInfo (automatically handles EST UTC-5 and EDT UTC-4 daylight saving)
US_ET = ZoneInfo("America/New_York")

# Official NYSE / NASDAQ Trading Holidays (2025 - 2026)
US_HOLIDAYS = {
    # 2025
    "2025-01-01",  # New Year's Day
    "2025-01-20",  # Martin Luther King, Jr. Day
    "2025-02-17",  # Washington's Birthday (Presidents' Day)
    "2025-04-18",  # Good Friday
    "2025-05-26",  # Memorial Day
    "2025-06-19",  # Juneteenth National Independence Day
    "2025-07-04",  # Independence Day
    "2025-09-01",  # Labor Day
    "2025-11-27",  # Thanksgiving Day
    "2025-12-25",  # Christmas Day
    # 2026
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # Martin Luther King, Jr. Day
    "2026-02-16",  # Washington's Birthday (Presidents' Day)
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth National Independence Day
    "2026-07-03",  # Independence Day (Observed)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving Day
    "2026-12-25",  # Christmas Day
}

def _calculate_easter(year: int):
    """Anonymous Gregorian computus algorithm for Western Easter."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    from datetime import date
    return date(year, month, day)


def _get_nth_weekday_of_month(year: int, month: int, weekday: int, n: int):
    from datetime import date, timedelta
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    d += timedelta(days=offset)
    d += timedelta(weeks=n - 1)
    return d


def _get_last_weekday_of_month(year: int, month: int, weekday: int):
    from datetime import date, timedelta
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    d = next_month - timedelta(days=1)
    offset = (d.weekday() - weekday) % 7
    return d - timedelta(days=offset)


def _get_observed_us_holiday(d):
    from datetime import timedelta
    if d.weekday() == 5:
        return d - timedelta(days=1)
    elif d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def is_exchange_holiday(target_date, is_us: bool = False) -> bool:
    """
    Robust holiday verification across calendar years:
    - Matches official published schedules for 2025 and 2026.
    - Accurately extends to future calendar years (2027+) via federal & exchange rules.
    """
    from datetime import date, timedelta
    d_obj = target_date if isinstance(target_date, date) else target_date.date()
    d_str = d_obj.strftime("%Y-%m-%d")
    year = d_obj.year

    if is_us:
        if d_str in US_HOLIDAYS:
            return True
        easter_d = _calculate_easter(year)
        good_friday = easter_d - timedelta(days=2)
        us_dyn_holidays = {
            _get_observed_us_holiday(date(year, 1, 1)),                  # New Year's Day
            _get_nth_weekday_of_month(year, 1, 0, 3),                 # MLK Day (3rd Mon Jan)
            _get_nth_weekday_of_month(year, 2, 0, 3),                 # Presidents' Day (3rd Mon Feb)
            good_friday,                                              # Good Friday
            _get_last_weekday_of_month(year, 5, 0),                   # Memorial Day (Last Mon May)
            _get_observed_us_holiday(date(year, 6, 19)),                 # Juneteenth
            _get_observed_us_holiday(date(year, 7, 4)),                  # Independence Day
            _get_nth_weekday_of_month(year, 9, 0, 1),                 # Labor Day (1st Mon Sep)
            _get_nth_weekday_of_month(year, 11, 3, 4),                # Thanksgiving (4th Thu Nov)
            _get_observed_us_holiday(date(year, 12, 25))                 # Christmas Day
        }
        return d_obj in us_dyn_holidays
    else:
        if d_str in NSE_HOLIDAYS:
            return True
        easter_d = _calculate_easter(year)
        good_friday = easter_d - timedelta(days=2)
        fixed_nse_holidays = {
            date(year, 1, 26),   # Republic Day
            date(year, 4, 14),   # Dr. Ambedkar Jayanti
            good_friday,         # Good Friday
            date(year, 5, 1),    # Maharashtra Day
            date(year, 8, 15),   # Independence Day
            date(year, 10, 2),   # Mahatma Gandhi Jayanti
            date(year, 12, 25)   # Christmas
        }
        return d_obj in fixed_nse_holidays


def is_us_symbol(symbol: Optional[str]) -> bool:
    """Determine whether a symbol belongs to US exchange (NYSE/NASDAQ)."""
    if not symbol:
        return False
    s = str(symbol).strip().upper()
    if s.endswith(".NS") or s.endswith(".BO"):
        return False
    us_tickers = {"AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "SPY", "QQQ", "PRX", "PROSY"}
    if s in us_tickers:
        return True
    if "." not in s and "^" not in s and s in STOCK_METADATA and STOCK_METADATA[s].get("market_cap", "").startswith("$"):
        return True
    return False


def get_market_status(as_of: Optional[Any] = None, symbol: Optional[str] = None) -> Tuple[str, str]:
    """
    Returns authoritative market status: 'OPEN', 'CLOSED', or 'STATUS UNKNOWN', and formatted exchange time.
    - Indian (NSE/BSE): Mon-Fri 09:15-15:30 IST, excluding official NSE_HOLIDAYS & national holidays.
    - US (NYSE/NASDAQ): Mon-Fri 09:30-16:00 US Eastern Time (ET/EDT), excluding official US_HOLIDAYS.
    Completely independent of individual provider connectivity or single API request success.
    """
    try:
        is_us = is_us_symbol(symbol)
        tz = US_ET if is_us else IST
        if as_of:
            now_dt = as_of.astimezone(tz) if hasattr(as_of, "astimezone") and as_of.tzinfo else as_of.replace(tzinfo=tz)
        else:
            now_dt = datetime.now(tz)

        time_str = now_dt.strftime("%I:%M:%S %p")

        if is_exchange_holiday(now_dt.date(), is_us=is_us):
            return "CLOSED", time_str

        # Weekend Check (Saturday=5, Sunday=6)
        if now_dt.weekday() >= 5:
            return "CLOSED", time_str

        # Regular Trading Hours
        if is_us:
            market_open = now_dt.replace(hour=9, minute=30, second=0, microsecond=0)
            market_close = now_dt.replace(hour=16, minute=0, second=0, microsecond=0)
        else:
            market_open = now_dt.replace(hour=9, minute=15, second=0, microsecond=0)
            market_close = now_dt.replace(hour=15, minute=30, second=0, microsecond=0)

        if market_open <= now_dt <= market_close:
            return "OPEN", time_str
        else:
            return "CLOSED", time_str
    except Exception:
        fallback_time = datetime.now().strftime("%I:%M:%S %p")
        return "STATUS UNKNOWN", fallback_time


def format_market_cap(mcap: Optional[float], currency: str = "INR") -> str:
    """Format market capitalization into localized financial notation."""
    if not mcap or mcap <= 0 or np.isnan(mcap):
        return "N/A"
    if currency == "INR":
        if mcap >= 1_000_000_000_000:
            return f"₹{mcap / 1_000_000_000_000:.2f} Lakh Cr"
        elif mcap >= 10_000_000:
            return f"₹{mcap / 10_000_000:,.0f} Cr"
        return f"₹{mcap:,.0f}"
    else:
        sym = "$" if currency == "USD" else f"{currency} "
        if mcap >= 1_000_000_000_000:
            return f"{sym}{mcap / 1_000_000_000_000:.2f}T"
        elif mcap >= 1_000_000_000:
            return f"{sym}{mcap / 1_000_000_000:.2f}B"
        elif mcap >= 1_000_000:
            return f"{sym}{mcap / 1_000_000:.2f}M"
        return f"{sym}{mcap:,.0f}"


def generate_fallback_candles(symbol: str, range_key: str) -> pd.DataFrame:
    """Returns an empty candles DataFrame when provider data is unavailable.
    Never fabricates artificial prices, sine-waves, or synthetic timestamps.
    """
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])



def get_stock_visualization_data(symbol: str, time_range: str = "1D") -> Dict[str, Any]:
    """
    Fetches real stock visualization data across selectable time ranges:
    1D, 5D, 1M, 6M, 1Y.
    Strictly uses 1-minute intraday intervals for 1D monitoring.
    Probes primary (NSE) and alternate (BSE) feeds to deliver the freshest provider candles.
    Never fakes timestamps; accurately classifies data status (LIVE, DELAYED, STALE, CLOSED).
    """
    global _RANGE_CACHE
    norm_sym = normalize_ticker(symbol)
    clean_sym = get_clean_symbol(norm_sym)
    range_key = time_range.upper()
    if range_key not in {"1D", "5D", "1M", "6M", "1Y"}:
        range_key = "1D"

    cache_id = (clean_sym, range_key)
    now_ts = time.time()

    # Determine dynamic TTL based on market open/closed status
    market_status_val, _ = get_market_status(symbol=norm_sym)
    is_market_open = (market_status_val == "OPEN")

    if not is_market_open:
        # Market is closed (historical candles never change until next trading session)
        ttl = CACHE_TTL_CLOSED
    elif range_key == "1D":
        ttl = CACHE_TTL_INTRADAY
    else:
        ttl = CACHE_TTL_HISTORICAL

    if cache_id in _RANGE_CACHE:
        entry = _RANGE_CACHE[cache_id]
        if now_ts - entry["timestamp"] < ttl:
            cached_market_status = entry["data"].get("market_status")
            if is_market_open and cached_market_status == "CLOSED" and range_key == "1D":
                _RANGE_CACHE.pop(cache_id, None)
            else:
                data_copy = entry["data"].copy()
                data_copy["market_status"] = market_status_val
                data_copy["trading_status"] = "Normal Trading" if is_market_open else "Market Closed"
                if not is_market_open:
                    data_copy["data_status"] = "CLOSED"
                else:
                    latest_ts = data_copy.get("latest_market_timestamp")
                    if latest_ts:
                        try:
                            latest_dt = datetime.fromisoformat(latest_ts)
                            target_tz = US_ET if is_us_symbol(norm_sym) else IST
                            if not latest_dt.tzinfo:
                                latest_dt = latest_dt.replace(tzinfo=target_tz)
                            age = max(0, int((datetime.now(timezone.utc) - latest_dt.astimezone(timezone.utc)).total_seconds()))
                            data_copy["data_age_seconds"] = age
                            if age <= 180:
                                data_copy["data_status"] = "LIVE"
                            elif age <= 900:
                                data_copy["data_status"] = "DELAYED"
                            else:
                                data_copy["data_status"] = "STALE"
                        except Exception:
                            pass
                return data_copy

    # In-flight request deduplication: if another thread is currently fetching this exact item, wait for it
    event_to_wait = None
    is_fetcher = False
    with _IN_FLIGHT_LOCK:
        if cache_id in _IN_FLIGHT_EVENTS:
            event_to_wait = _IN_FLIGHT_EVENTS[cache_id]
        else:
            event_to_wait = threading.Event()
            _IN_FLIGHT_EVENTS[cache_id] = event_to_wait
            is_fetcher = True

    if not is_fetcher and event_to_wait is not None:
        event_to_wait.wait(timeout=8.0)
        if cache_id in _RANGE_CACHE:
            entry = _RANGE_CACHE[cache_id]
            data_copy = entry["data"].copy()
            data_copy["market_status"] = market_status_val
            data_copy["trading_status"] = "Normal Trading" if is_market_open else "Market Closed"
            if not is_market_open:
                data_copy["data_status"] = "CLOSED"
            else:
                latest_ts = data_copy.get("latest_market_timestamp")
                if latest_ts:
                    try:
                        latest_dt = datetime.fromisoformat(latest_ts)
                        if not latest_dt.tzinfo:
                            latest_dt = latest_dt.replace(tzinfo=IST)
                        age = max(0, int((datetime.now(IST) - latest_dt).total_seconds()))
                        data_copy["data_age_seconds"] = age
                        if age <= 180:
                            data_copy["data_status"] = "LIVE"
                        elif age <= 900:
                            data_copy["data_status"] = "DELAYED"
                        else:
                            data_copy["data_status"] = "STALE"
                    except Exception:
                        pass
            return data_copy

    range_config = {
        "1D": {"period": "1d", "interval": "1m"},
        "5D": {"period": "5d", "interval": "5m"},
        "1M": {"period": "1mo", "interval": "1d"},
        "6M": {"period": "6mo", "interval": "1d"},
        "1Y": {"period": "1y", "interval": "1d"}
    }
    cfg = range_config[range_key]
    server_time = datetime.now(IST)
    if clean_sym in _NEGATIVE_LOOKUP_CACHE:
        neg_entry = _NEGATIVE_LOOKUP_CACHE[clean_sym]
        neg_ts = neg_entry["timestamp"] if isinstance(neg_entry, dict) else float(neg_entry)
        neg_ttl = neg_entry.get("ttl", NEGATIVE_CACHE_TTL_TRANSIENT) if isinstance(neg_entry, dict) else NEGATIVE_CACHE_TTL_TRANSIENT
        if now_ts - neg_ts < neg_ttl:
            meta = STOCK_METADATA.get(clean_sym, {})
            display_name = meta.get("name", f"{clean_sym} Equity")
            return {
                "status": "DATA_UNAVAILABLE",
                "data_status": "DATA_UNAVAILABLE",
                "data_interval": cfg["interval"],
                "data_age_seconds": None,
                "latest_market_timestamp": None,
                "data_received_at": server_time.isoformat(),
                "provider": "yfinance",
                "symbol": clean_sym,
                "full_symbol": f"{clean_sym}.NS",
                "name": display_name,
                "currency_symbol": "₹",
                "exchange": "NSE",
                "price": 0.0,
                "previous_close": 0.0,
                "price_change": 0.0,
                "change_pct": 0.0,
                "day_high": 0.0,
                "day_low": 0.0,
                "open": 0.0,
                "volume": "N/A",
                "raw_volume": 0,
                "fifty_two_week_high": None,
                "fifty_two_week_low": None,
                "market_cap": "N/A",
                "avg_volume": "N/A",
                "trading_status": "Data Unavailable",
                "market_status": market_status_val,
                "last_updated": server_time.strftime("%I:%M:%S %p"),
                "market_last_traded": "N/A",
                "time_range": range_key,
                "candles": [],
                "chart_points": [],
                "supertrend": None
            }

    try:
        # Determine candidate tickers to fetch (deduplicated):
        raw_upper = symbol.strip().upper()
        resolved_prev = _RESOLVED_TICKER_CACHE.get(clean_sym)
        mapped = TICKER_ALIASES.get(raw_upper, TICKER_ALIASES.get(clean_sym))
        candidates_raw = []
        if resolved_prev:
            candidates_raw.append(resolved_prev)
        if mapped:
            candidates_raw.extend([mapped, f"{clean_sym}.NS", f"{clean_sym}.BO"])
        elif "." in raw_upper or "^" in raw_upper:
            candidates_raw.append(raw_upper)
        elif clean_sym in {"AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "SPY", "QQQ", "PRX", "PROSY"}:
            candidates_raw.append(clean_sym)
        else:
            candidates_raw.extend([f"{clean_sym}.NS", f"{clean_sym}.BO"])

        candidate_syms = list(dict.fromkeys(candidates_raw))

        best_df = pd.DataFrame()
        best_ticker = None
        best_chosen_sym = candidate_syms[0]
        had_network_exception = False

        for cand_idx, cand in enumerate(candidate_syms):
            try:
                t = _get_yf().Ticker(cand)
                # Primary candidate gets YFINANCE_PRIMARY_TIMEOUT; secondary fallbacks get shorter YFINANCE_SECONDARY_TIMEOUT
                cand_timeout = YFINANCE_PRIMARY_TIMEOUT if cand_idx == 0 else YFINANCE_SECONDARY_TIMEOUT
                df_cand = t.history(period=cfg["period"], interval=cfg["interval"], auto_adjust=False, timeout=cand_timeout)

                # Robust multi-tier fallback for 1D when market is closed or 1d 1m is empty
                if df_cand.empty and range_key == "1D":
                    fb_timeout = min(cand_timeout, YFINANCE_FALLBACK_TIMEOUT)
                    # 1. Retrieve the latest available 1m intraday candles from the last 5 days
                    try:
                        df_cand = t.history(period="5d", interval="1m", auto_adjust=False, timeout=fb_timeout)
                        if not df_cand.empty:
                            last_date = df_cand.index[-1].date()
                            df_cand = df_cand[df_cand.index.date == last_date]
                    except Exception:
                        pass

                    # 2. If 1m data is purged/unavailable (e.g. closed market / holiday), get 5m candles of latest session
                    if df_cand.empty:
                        try:
                            df_cand = t.history(period="5d", interval="5m", auto_adjust=False, timeout=fb_timeout)
                            if not df_cand.empty:
                                last_date = df_cand.index[-1].date()
                                df_cand = df_cand[df_cand.index.date == last_date]
                        except Exception:
                            pass

                    # 3. If intraday is unavailable from provider, fall back to recent daily candles
                    if df_cand.empty:
                        try:
                            df_cand = t.history(period="1mo", interval="1d", auto_adjust=False, timeout=fb_timeout)
                        except Exception:
                            pass

                if not df_cand.empty:
                    best_df = df_cand
                    best_ticker = t
                    best_chosen_sym = cand
                    _RESOLVED_TICKER_CACHE[clean_sym] = cand
                    break
            except Exception as e_cand:
                had_network_exception = True
                print(f"[MarketData] Note checking candidate {cand}: {e_cand}")

        df = best_df
        ticker = best_ticker
        norm_sym = best_chosen_sym

        if df.empty or len(df) < 1:
            is_known = bool(clean_sym in STOCK_METADATA or clean_sym in TICKER_ALIASES)
            is_syntactically_invalid = bool(not clean_sym.replace("^", "").isalnum() or len(clean_sym) > 15)
            # Distinguish transient network issues from invalid or unsupported tickers
            if is_syntactically_invalid or (not is_known and not had_network_exception):
                neg_reason = "INVALID_SYMBOL"
                neg_ttl = NEGATIVE_CACHE_TTL_INVALID
            else:
                neg_reason = "TRANSIENT"
                neg_ttl = NEGATIVE_CACHE_TTL_TRANSIENT

            _NEGATIVE_LOOKUP_CACHE[clean_sym] = {
                "timestamp": now_ts,
                "reason": neg_reason,
                "ttl": neg_ttl,
                "error": "Empty candle data returned"
            }
            print(f"[MarketData] Symbol: {clean_sym} | Provider empty ({neg_reason}, cache TTL: {neg_ttl}s).")
            meta = STOCK_METADATA.get(clean_sym, {})
            currency_symbol = "₹" if (".NS" in norm_sym or ".BO" in norm_sym) else "$"
            exchange_label = "NSE" if ".NS" in norm_sym else ("BSE" if ".BO" in norm_sym else "NASDAQ")
            display_name = meta.get("name", f"{clean_sym} Equity")
            return {
                "status": "DATA_UNAVAILABLE",
                "data_status": "DATA_UNAVAILABLE",
                "data_interval": cfg["interval"],
                "data_age_seconds": None,
                "latest_market_timestamp": None,
                "data_received_at": server_time.isoformat(),
                "provider": "yfinance",
                "symbol": clean_sym,
                "full_symbol": norm_sym,
                "name": display_name,
                "currency_symbol": currency_symbol,
                "exchange": exchange_label,
                "price": 0.0,
                "previous_close": 0.0,
                "price_change": 0.0,
                "change_pct": 0.0,
                "day_high": 0.0,
                "day_low": 0.0,
                "open": 0.0,
                "volume": "N/A",
                "raw_volume": 0,
                "fifty_two_week_high": None,
                "fifty_two_week_low": None,
                "market_cap": "N/A",
                "avg_volume": "N/A",
                "trading_status": "Data Unavailable",
                "market_status": market_status_val,
                "last_updated": server_time.strftime("%I:%M:%S %p"),
                "market_last_traded": "N/A",
                "time_range": range_key,
                "candles": [],
                "chart_points": [],
                "supertrend": None
            }

        df.columns = [c.lower() for c in df.columns]
        df = df.sort_index()
        df = df[~df.index.duplicated(keep='last')]

        # Automatic recovery from temporary error: clean up negative cache entry
        _NEGATIVE_LOOKUP_CACHE.pop(clean_sym, None)

        # Fast info & metadata extraction with local 2-hour caching (static fields only, NEVER cache price for 2 hours)
        fi_cached = _FAST_INFO_CACHE.get(clean_sym)
        if fi_cached and (now_ts - fi_cached.get("ts", 0) < FAST_INFO_CACHE_TTL):
            fi_currency = fi_cached.get("currency", "INR" if (".NS" in norm_sym or ".BO" in norm_sym) else "USD")
            fi_prev_close = fi_cached.get("previous_close")
            fi_open = fi_cached.get("open")
            fi_year_high = fi_cached.get("year_high")
            fi_year_low = fi_cached.get("year_low")
            fi_market_cap = fi_cached.get("market_cap")
            fi_avg_vol = fi_cached.get("avg_vol")
        else:
            fi_prev_close = None
            fi_open = None
            fi_year_high = None
            fi_year_low = None
            fi_market_cap = None
            fi_avg_vol = None
            fi_currency = "INR" if (".NS" in norm_sym or ".BO" in norm_sym) else "USD"

            if ticker:
                try:
                    fi = getattr(ticker, 'fast_info', None)
                    if fi:
                        fi_currency = getattr(fi, 'currency', fi_currency) or fi_currency
                        fi_prev_close = getattr(fi, 'previous_close', None) or getattr(fi, 'previousClose', None)
                        fi_open = getattr(fi, 'open', None)
                        fi_year_high = getattr(fi, 'year_high', None) or getattr(fi, 'yearHigh', None)
                        fi_year_low = getattr(fi, 'year_low', None) or getattr(fi, 'yearLow', None)
                        fi_market_cap = getattr(fi, 'market_cap', None) or getattr(fi, 'marketCap', None)
                        fi_avg_vol = getattr(fi, 'three_month_average_volume', None) or getattr(fi, 'ten_day_average_volume', None)

                        if len(_FAST_INFO_CACHE) > 512:
                            _FAST_INFO_CACHE.pop(next(iter(_FAST_INFO_CACHE)), None)
                        _FAST_INFO_CACHE[clean_sym] = {
                            "ts": now_ts,
                            "currency": fi_currency,
                            "previous_close": fi_prev_close,
                            "open": fi_open,
                            "year_high": fi_year_high,
                            "year_low": fi_year_low,
                            "market_cap": fi_market_cap,
                            "avg_vol": fi_avg_vol
                        }
                except Exception:
                    pass

        # Determine best current price from fresh candle data or fresh fast_info (never static 2-hour cache)
        curr_price = float(df['close'].iloc[-1])
        if ticker:
            try:
                fi_fresh = getattr(ticker, 'fast_info', None)
                if fi_fresh:
                    fresh_p = getattr(fi_fresh, 'last_price', None) or getattr(fi_fresh, 'lastPrice', None)
                    if fresh_p is not None and isinstance(fresh_p, (int, float, np.floating, np.integer)) and not np.isnan(fresh_p) and fresh_p > 0:
                        curr_price = float(fresh_p)
            except Exception:
                pass

        # Authoritative market status check (timezone, holidays, weekends, trading hours)
        market_status_val, ist_time_str = get_market_status(server_time, symbol=norm_sym)
        is_market_currently_open = (market_status_val == "OPEN")

        day_open = float(df['open'].iloc[0])

        # Localize latest candle timestamp to IST
        latest_idx = df.index[-1]
        latest_market_dt = latest_idx.astimezone(IST) if hasattr(latest_idx, 'astimezone') else pd.Timestamp(latest_idx, tz=IST)

        latest_market_timestamp = latest_market_dt.isoformat()
        data_received_at = server_time.isoformat()

        # Calculate data age & freshness status from genuine provider candle
        data_age_seconds = max(0, int((server_time - latest_market_dt).total_seconds()))

        if is_market_currently_open:
            if data_age_seconds <= 180:
                data_status = "LIVE"
            elif data_age_seconds <= 900:  # <= 15 minutes
                data_status = "DELAYED"
            else:
                data_status = "STALE"
        else:
            data_status = "CLOSED"

        # Validate & log market data strictly as specified (Section 2)
        print(f"[MarketData] Symbol: {clean_sym} | Requested interval: {cfg['interval']} | Server time: {server_time.strftime('%I:%M:%S %p')} | Latest market candle: {latest_market_dt.strftime('%I:%M:%S %p')} | Candles received: {len(df)} | Status: {data_status} | Feed: {norm_sym}")

        currency_symbol = "₹" if fi_currency == "INR" else ("$" if fi_currency == "USD" else f"{fi_currency} ")

        if range_key == "1D":
            if fi_prev_close is not None and isinstance(fi_prev_close, (int, float, np.floating, np.integer)) and not np.isnan(fi_prev_close) and fi_prev_close > 0:
                prev_close = float(fi_prev_close)
            else:
                prev_close = float(df['open'].iloc[0])
        else:
            prev_close = float(df['close'].iloc[0])

        if fi_open is not None and isinstance(fi_open, (int, float, np.floating, np.integer)) and not np.isnan(fi_open) and fi_open > 0:
            open_price = float(fi_open)
        else:
            open_price = day_open

        price_change = curr_price - prev_close
        change_pct = (price_change / prev_close) * 100.0 if prev_close else 0.0

        day_high = float(df['high'].max())
        day_low = float(df['low'].min())
        curr_vol = float(df['volume'].sum()) if range_key == "1D" else float(df['volume'].iloc[-1])

        meta = STOCK_METADATA.get(clean_sym, {
            "name": f"{clean_sym}",
            "market_cap": "N/A",
            "52w_high": None,
            "52w_low": None,
            "avg_vol": "N/A"
        })

        if fi_year_high is not None and isinstance(fi_year_high, (int, float, np.floating, np.integer)) and not np.isnan(fi_year_high):
            high_52w = round(float(fi_year_high), 2)
        elif range_key == "1Y" and not df.empty and 'high' in df and len(df) >= 200:
            high_52w = round(float(df['high'].max()), 2)
        else:
            high_52w = None

        if fi_year_low is not None and isinstance(fi_year_low, (int, float, np.floating, np.integer)) and not np.isnan(fi_year_low):
            low_52w = round(float(fi_year_low), 2)
        elif range_key == "1Y" and not df.empty and 'low' in df and len(df) >= 200:
            low_52w = round(float(df['low'].min()), 2)
        else:
            low_52w = None

        if fi_market_cap is not None and isinstance(fi_market_cap, (int, float, np.floating, np.integer)) and not np.isnan(fi_market_cap):
            market_cap_str = format_market_cap(fi_market_cap, fi_currency)
        else:
            market_cap_str = meta.get("market_cap", "N/A")

        if fi_avg_vol is not None and isinstance(fi_avg_vol, (int, float, np.floating, np.integer)) and not np.isnan(fi_avg_vol):
            avg_vol_str = format_volume_metric(float(fi_avg_vol))
        else:
            avg_vol_str = meta.get("avg_vol", "N/A")

        # Format chart candles chronologically with deduplication (vectorized conversion)
        chart_points = []
        seen_stamps = set()
        n_rows = len(df)
        if n_rows > 0:
            dts = df.index
            if hasattr(dts, "tz") and dts.tz is not None:
                dts_ist = dts.tz_convert(IST)
            else:
                dts_ist = dts.tz_localize(IST)

            date_fmt = "%I:%M %p" if range_key in {"1D", "5D"} else "%d %b"
            time_labels = dts_ist.strftime(date_fmt).tolist()
            iso_keys = [ts.isoformat() for ts in dts_ist]

            close_vals = np.round(df['close'].to_numpy(dtype=float), 2).tolist()
            open_vals = np.round(df['open'].to_numpy(dtype=float), 2).tolist()
            high_vals = np.round(df['high'].to_numpy(dtype=float), 2).tolist()
            low_vals = np.round(df['low'].to_numpy(dtype=float), 2).tolist()
            vol_vals = df['volume'].to_numpy(dtype=int).tolist() if 'volume' in df else [0] * n_rows

            for i in range(n_rows):
                iso_key = iso_keys[i]
                if iso_key in seen_stamps:
                    continue
                seen_stamps.add(iso_key)

                chart_points.append({
                    "time": time_labels[i],
                    "full_time": iso_key,
                    "price": close_vals[i],
                    "open": open_vals[i],
                    "high": high_vals[i],
                    "low": low_vals[i],
                    "volume": vol_vals[i]
                })

        exchange_label = "NSE" if ".NS" in norm_sym else ("BSE" if ".BO" in norm_sym else "NASDAQ")
        # Real-time server sync timestamp for active frontend polling clock
        last_updated_str = server_time.strftime("%I:%M:%S %p")
        market_last_traded_str = latest_market_dt.strftime("%I:%M:%S %p")

        requested_sym = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
        display_sym = requested_sym if requested_sym in TICKER_ALIASES else clean_sym
        display_name = meta.get("name") or (STOCK_METADATA.get(requested_sym, {}).get("name") if requested_sym in STOCK_METADATA else f"{display_sym} Equity")

        result = {
            "status": data_status,
            "data_status": data_status,
            "data_interval": cfg["interval"],
            "data_age_seconds": data_age_seconds,
            "latest_market_timestamp": latest_market_timestamp,
            "data_received_at": data_received_at,
            "provider": "yfinance",
            "symbol": display_sym,
            "full_symbol": norm_sym,
            "name": display_name,
            "currency_symbol": currency_symbol,
            "exchange": exchange_label,
            "price": round(curr_price, 2),
            "previous_close": round(prev_close, 2),
            "price_change": round(price_change, 2),
            "change_pct": round(change_pct, 2),
            "day_high": round(day_high, 2),
            "day_low": round(day_low, 2),
            "open": round(open_price, 2),
            "volume": format_volume_metric(curr_vol),
            "raw_volume": int(curr_vol),
            "fifty_two_week_high": high_52w,
            "fifty_two_week_low": low_52w,
            "market_cap": market_cap_str,
            "avg_volume": avg_vol_str,
            "trading_status": "Normal Trading" if is_market_currently_open else "Market Closed",
            "market_status": market_status_val,
            "last_updated": last_updated_str,
            "market_last_traded": market_last_traded_str,
            "time_range": range_key,
            "candles": chart_points,
            "chart_points": chart_points
        }

        # Pre-compute SuperTrend so frontend does not need a separate roundtrip
        try:
            from app.services.feature_engine import analyze_supertrend_hit
            st_analysis = analyze_supertrend_hit(
                candles_or_df=chart_points,
                current_price=round(curr_price, 2),
                period=10,
                multiplier=3.0,
                proximity_points=1.0,
                candle_interval=cfg["interval"],
                now=server_time,
                is_market_open=is_market_currently_open,
                data_status=data_status
            )
            result["supertrend"] = {
                "symbol": display_sym,
                "name": display_name,
                "price": round(curr_price, 2),
                "supertrend_price": st_analysis.get("supertrend_price"),
                "direction": st_analysis.get("direction"),
                "previous_direction": st_analysis.get("previous_direction"),
                "target_direction": st_analysis.get("target_direction"),
                "distance_points": st_analysis.get("distance_points"),
                "proximity_threshold": st_analysis.get("proximity_threshold"),
                "is_approaching": st_analysis.get("is_approaching"),
                "is_prediction_ready": st_analysis.get("is_prediction_ready"),
                "is_flipped": st_analysis.get("is_flipped"),
                "flip_signal": st_analysis.get("flip_signal"),
                "provisional_direction": st_analysis.get("provisional_direction"),
                "provisional_supertrend_price": st_analysis.get("provisional_supertrend_price"),
                "penetration_atr_ratio": st_analysis.get("penetration_atr_ratio"),
                "candle_open": st_analysis.get("candle_open"),
                "candle_close": st_analysis.get("candle_close"),
                "candle_points": st_analysis.get("candle_points"),
                "candle_color": st_analysis.get("candle_color"),
                "candle_body_size": st_analysis.get("candle_body_size"),
                "is_candle_stable": st_analysis.get("is_candle_stable"),
                "candle_matches_target": st_analysis.get("candle_matches_target"),
                "candle_age_seconds": st_analysis.get("candle_age_seconds"),
                "candle_timestamp": st_analysis.get("candle_timestamp"),
                "series": st_analysis.get("series", [])
            }
        except Exception:
            result["supertrend"] = None

        # Cache result
        if len(_RANGE_CACHE) > 512:
            _RANGE_CACHE.pop(next(iter(_RANGE_CACHE)), None)
        _RANGE_CACHE[cache_id] = {
            "data": result,
            "timestamp": now_ts
        }
        return result

    except Exception as e:
        if len(_NEGATIVE_LOOKUP_CACHE) > 512:
            _NEGATIVE_LOOKUP_CACHE.pop(next(iter(_NEGATIVE_LOOKUP_CACHE)), None)
        _NEGATIVE_LOOKUP_CACHE[clean_sym] = {
            "timestamp": now_ts,
            "reason": "TRANSIENT",
            "ttl": NEGATIVE_CACHE_TTL_TRANSIENT,
            "error": str(e)
        }
        print(f"[MarketData] Error retrieving visualization for {symbol}: {e}")
        return {
            "status": "UNAVAILABLE",
            "data_status": "UNAVAILABLE",
            "error": "Market data unavailable",
            "symbol": clean_sym,
            "name": clean_sym,
            "time_range": range_key,
            "provider": "yfinance",
            "latest_market_timestamp": None,
            "data_received_at": server_time.isoformat(),
            "data_interval": cfg["interval"],
            "data_age_seconds": None,
            "last_updated": "--:--:--"
        }
    finally:
        if is_fetcher and event_to_wait is not None:
            with _IN_FLIGHT_LOCK:
                event_to_wait.set()
                _IN_FLIGHT_EVENTS.pop(cache_id, None)


def get_negative_cache_status() -> Dict[str, Any]:
    """Returns telemetry of symbols currently in the negative market data cache."""
    now_ts = time.time()
    active_neg = {}
    for sym, entry in list(_NEGATIVE_LOOKUP_CACHE.items()):
        ts = entry["timestamp"] if isinstance(entry, dict) else float(entry)
        ttl = entry.get("ttl", NEGATIVE_CACHE_TTL_TRANSIENT) if isinstance(entry, dict) else NEGATIVE_CACHE_TTL_TRANSIENT
        elapsed = now_ts - ts
        if elapsed < ttl:
            active_neg[sym] = {
                "reason": entry.get("reason", "TRANSIENT") if isinstance(entry, dict) else "TRANSIENT",
                "remaining_seconds": round(ttl - elapsed, 1),
                "ttl": ttl,
                "error": entry.get("error") if isinstance(entry, dict) else None
            }
    return active_neg


def clear_negative_cache(symbol: Optional[str] = None):
    """Manually clear negative lookup cache for a symbol or all symbols."""
    global _NEGATIVE_LOOKUP_CACHE
    if symbol:
        clean = get_clean_symbol(normalize_ticker(symbol))
        _NEGATIVE_LOOKUP_CACHE.pop(clean, None)
    else:
        _NEGATIVE_LOOKUP_CACHE.clear()


_WARMER_STARTED = False

def warmup_popular_stocks():
    """Asynchronously pre-warms cache for popular symbols in a background thread pool."""
    global _WARMER_STARTED
    if _WARMER_STARTED:
        return
    _WARMER_STARTED = True

    def _warmup_worker():
        time.sleep(2.5)
        from concurrent.futures import ThreadPoolExecutor
        popular = ["TCS", "INFY", "RELIANCE", "HDFCBANK", "SBIN", "ICICIBANK"]
        with ThreadPoolExecutor(max_workers=3) as executor:
            for sym in popular:
                if (sym, "1D") not in _RANGE_CACHE:
                    executor.submit(get_stock_visualization_data, sym, "1D")

    t = threading.Thread(target=_warmup_worker, daemon=True, name="StockPulseCacheWarmer")
    t.start()



def get_stock_data_status(symbol: str) -> Dict[str, Any]:
    """
    Diagnostic endpoint data generator (Section 14).
    Exposes provider timestamp, server timestamp, data age, and status.
    """
    data = get_stock_visualization_data(symbol, time_range="1D")
    now_ist = datetime.now(IST)
    return {
        "symbol": data.get("symbol", symbol),
        "provider": data.get("provider", "yfinance"),
        "interval": data.get("data_interval", "1m"),
        "latest_market_timestamp": data.get("latest_market_timestamp"),
        "server_timestamp": now_ist.isoformat(),
        "data_age_seconds": data.get("data_age_seconds", 0),
        "status": data.get("data_status", "LIVE"),
        "exchange": data.get("exchange", "NSE"),
        "last_updated": data.get("last_updated")
    }


def get_available_stocks_list() -> List[Dict[str, Any]]:
    """Returns curated list of searchable stocks for instant autocomplete."""
    stocks = [
        {"symbol": "TCS", "name": "Tata Consultancy Services Ltd", "exchange": "NSE"},
        {"symbol": "INFY", "name": "Infosys Ltd", "exchange": "NSE"},
        {"symbol": "RELIANCE", "name": "Reliance Industries Ltd", "exchange": "NSE"},
        {"symbol": "HDFCBANK", "name": "HDFC Bank Ltd", "exchange": "NSE"},
        {"symbol": "ICICIBANK", "name": "ICICI Bank Ltd", "exchange": "NSE"},
        {"symbol": "SBIN", "name": "State Bank of India", "exchange": "NSE"},
        {"symbol": "BHARTIARTL", "name": "Bharti Airtel Ltd", "exchange": "NSE"},
        {"symbol": "WIPRO", "name": "Wipro Ltd", "exchange": "NSE"},
        {"symbol": "OLX", "name": "OLX India / CarTrade Tech Ltd", "exchange": "NSE"},
        {"symbol": "CARTRADE", "name": "CarTrade Tech Ltd (OLX India)", "exchange": "NSE"},
        {"symbol": "ZOMATO", "name": "Zomato Ltd", "exchange": "NSE"},
        {"symbol": "PAYTM", "name": "One97 Communications Ltd (Paytm)", "exchange": "NSE"},
        {"symbol": "SWIGGY", "name": "Swiggy Ltd", "exchange": "NSE"},
        {"symbol": "NYKAA", "name": "FSN E-Commerce Ventures (Nykaa)", "exchange": "NSE"},
        {"symbol": "POLICYBZR", "name": "PB Fintech Ltd (PolicyBazaar)", "exchange": "NSE"},
        {"symbol": "TATAMOTORS", "name": "Tata Motors Ltd", "exchange": "NSE"},
        {"symbol": "TATASTEEL", "name": "Tata Steel Ltd", "exchange": "NSE"},
        {"symbol": "ITC", "name": "ITC Ltd", "exchange": "NSE"},
        {"symbol": "HINDUNILVR", "name": "Hindustan Unilever Ltd", "exchange": "NSE"},
        {"symbol": "LT", "name": "Larsen & Toubro Ltd", "exchange": "NSE"},
        {"symbol": "AXISBANK", "name": "Axis Bank Ltd", "exchange": "NSE"},
        {"symbol": "KOTAKBANK", "name": "Kotak Mahindra Bank Ltd", "exchange": "NSE"},
        {"symbol": "BAJFINANCE", "name": "Bajaj Finance Ltd", "exchange": "NSE"},
        {"symbol": "BAJAJFINSV", "name": "Bajaj Finserv Ltd", "exchange": "NSE"},
        {"symbol": "MARUTI", "name": "Maruti Suzuki India Ltd", "exchange": "NSE"},
        {"symbol": "SUNPHARMA", "name": "Sun Pharmaceutical Industries", "exchange": "NSE"},
        {"symbol": "TITAN", "name": "Titan Company Ltd", "exchange": "NSE"},
        {"symbol": "ULTRACEMCO", "name": "UltraTech Cement Ltd", "exchange": "NSE"},
        {"symbol": "ASIANPAINT", "name": "Asian Paints Ltd", "exchange": "NSE"},
        {"symbol": "NTPC", "name": "NTPC Ltd", "exchange": "NSE"},
        {"symbol": "ONGC", "name": "Oil & Natural Gas Corporation", "exchange": "NSE"},
        {"symbol": "POWERGRID", "name": "Power Grid Corporation of India", "exchange": "NSE"},
        {"symbol": "M&M", "name": "Mahindra & Mahindra Ltd", "exchange": "NSE"},
        {"symbol": "COALINDIA", "name": "Coal India Ltd", "exchange": "NSE"},
        {"symbol": "ADANIENT", "name": "Adani Enterprises Ltd", "exchange": "NSE"},
        {"symbol": "ADANIPORTS", "name": "Adani Ports and SEZ Ltd", "exchange": "NSE"},
        {"symbol": "JSWSTEEL", "name": "JSW Steel Ltd", "exchange": "NSE"},
        {"symbol": "TRENT", "name": "Trent Ltd", "exchange": "NSE"},
        {"symbol": "BEL", "name": "Bharat Electronics Ltd", "exchange": "NSE"},
        {"symbol": "HAL", "name": "Hindustan Aeronautics Ltd", "exchange": "NSE"},
        {"symbol": "VEDL", "name": "Vedanta Ltd", "exchange": "NSE"},
        {"symbol": "BHEL", "name": "Bharat Heavy Electricals Ltd", "exchange": "NSE"},
        {"symbol": "IRCTC", "name": "Indian Railway Catering & Tourism", "exchange": "NSE"},
        {"symbol": "DMART", "name": "Avenue Supermarts Ltd (DMart)", "exchange": "NSE"},
        {"symbol": "HCLTECH", "name": "HCL Technologies Ltd", "exchange": "NSE"},
        {"symbol": "TECHM", "name": "Tech Mahindra Ltd", "exchange": "NSE"},
        {"symbol": "NESTLEIND", "name": "Nestle India Ltd", "exchange": "NSE"},
        {"symbol": "GRASIM", "name": "Grasim Industries Ltd", "exchange": "NSE"},
        {"symbol": "CIPLA", "name": "Cipla Ltd", "exchange": "NSE"},
        {"symbol": "DRREDDY", "name": "Dr. Reddy's Laboratories", "exchange": "NSE"},
        {"symbol": "APOLLOHOSP", "name": "Apollo Hospitals Enterprise", "exchange": "NSE"},
        {"symbol": "EICHERMOT", "name": "Eicher Motors Ltd", "exchange": "NSE"},
        {"symbol": "DIVISLAB", "name": "Divi's Laboratories Ltd", "exchange": "NSE"},
        {"symbol": "HEROMOTOCO", "name": "Hero MotoCorp Ltd", "exchange": "NSE"},
        {"symbol": "BPCL", "name": "Bharat Petroleum Corp Ltd", "exchange": "NSE"},
        {"symbol": "INDUSINDBK", "name": "IndusInd Bank Ltd", "exchange": "NSE"},
        {"symbol": "HDFCLIFE", "name": "HDFC Life Insurance Co Ltd", "exchange": "NSE"},
        {"symbol": "SBILIFE", "name": "SBI Life Insurance Co Ltd", "exchange": "NSE"},
        {"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ"},
        {"symbol": "MSFT", "name": "Microsoft Corporation", "exchange": "NASDAQ"},
        {"symbol": "GOOGL", "name": "Alphabet Inc. (Google)", "exchange": "NASDAQ"},
        {"symbol": "AMZN", "name": "Amazon.com Inc.", "exchange": "NASDAQ"},
        {"symbol": "TSLA", "name": "Tesla Inc.", "exchange": "NASDAQ"},
        {"symbol": "NVDA", "name": "NVIDIA Corporation", "exchange": "NASDAQ"},
        {"symbol": "META", "name": "Meta Platforms Inc.", "exchange": "NASDAQ"}
    ]
    return stocks


def get_cached_latest_market_snapshot(symbol: str) -> Optional[Dict[str, Any]]:
    """Return the newest already-cached 1D market snapshot without network I/O.

    Used by fast UI polling endpoints. It deliberately never calls Yahoo Finance.
    """
    clean_sym = get_clean_symbol(normalize_ticker(symbol))
    entry = _RANGE_CACHE.get((clean_sym, "1D"))
    if not entry:
        return None
    data = entry.get("data") or {}
    if not data.get("price"):
        return None
    return {
        "symbol": data.get("symbol", symbol),
        "name": data.get("name", symbol),
        "price": data.get("price", 0.0),
        "previous_close": data.get("previous_close", data.get("price", 0.0)),
        "price_change": data.get("price_change", 0.0),
        "change_pct": data.get("change_pct", 0.0),
        "data_status": data.get("data_status", data.get("status", "LIVE")),
        "data_age_seconds": data.get("data_age_seconds", 0),
        "data_received_at": data.get("data_received_at"),
        "latest_market_timestamp": data.get("latest_market_timestamp"),
        "features": {}
    }


_SNAPSHOT_CACHE: Dict[str, Dict[str, Any]] = {}


def get_latest_market_snapshot(symbol: str) -> Dict[str, Any]:
    """Get latest snapshot for alert evaluation and ML features."""
    clean_sym_upper = symbol.strip().upper()
    data = get_stock_visualization_data(symbol, time_range="1D")
    latest_ts = str(data.get("latest_market_timestamp"))
    curr_price = round(float(data.get("price") or 0.0), 2)
    candles = data.get("candles", [])
    candle_count = len(candles)

    # Fast cached snapshot check: reuse if underlying candles and price have not changed
    cached_entry = _SNAPSHOT_CACHE.get(clean_sym_upper)
    if (
        cached_entry is not None
        and cached_entry.get("_ts") == latest_ts
        and cached_entry.get("_price") == curr_price
        and cached_entry.get("_count") == candle_count
    ):
        snap_copy = cached_entry["snapshot"].copy()
        snap_copy["data_status"] = data.get("data_status", "LIVE")
        snap_copy["data_age_seconds"] = data.get("data_age_seconds", 0)
        snap_copy["last_updated"] = data.get("last_updated", "")
        return snap_copy

    try:
        from app.services.feature_engine import compute_market_features
    except ImportError:
        compute_market_features = None

    features = {}
    from app.services.feature_engine import get_candle_sequence_key
    ckey = get_candle_sequence_key(candles)
    feat_key = (
        clean_sym_upper,
        latest_ts,
        ckey,
        curr_price
    )

    if feat_key in _SNAPSHOT_FEATURES_CACHE:
        features = _SNAPSHOT_FEATURES_CACHE[feat_key].copy()
    elif candles and len(candles) >= 5 and compute_market_features is not None:
        try:
            df = pd.DataFrame(candles)
            features_df = compute_market_features(df)
            if not features_df.empty:
                last_row = features_df.iloc[-1].to_dict()
                features = {k: float(v) for k, v in last_row.items() if isinstance(v, (int, float, np.floating, np.integer)) and not np.isnan(v)}
                if len(_SNAPSHOT_FEATURES_CACHE) > 256:
                    _SNAPSHOT_FEATURES_CACHE.pop(next(iter(_SNAPSHOT_FEATURES_CACHE)))
                _SNAPSHOT_FEATURES_CACHE[feat_key] = features.copy()
        except Exception as fe_err:
            print(f"[MarketData] Error computing features for {symbol}: {fe_err}")

    result = {
        "symbol": data.get("symbol", symbol),
        "name": data.get("name", symbol),
        "price": data.get("price") or 0.0,
        "previous_close": data.get("previous_close") or 0.0,
        "price_change": data.get("price_change") or 0.0,
        "change_pct": data.get("change_pct") or 0.0,
        "volume": data.get("raw_volume") or 0,
        "open": data.get("open") or 0.0,
        "high": data.get("day_high") or 0.0,
        "low": data.get("day_low") or 0.0,
        "last_updated": data.get("last_updated", ""),
        "latest_market_timestamp": data.get("latest_market_timestamp"),
        "data_received_at": data.get("data_received_at"),
        "data_status": data.get("data_status", "LIVE"),
        "data_age_seconds": data.get("data_age_seconds", 0),
        "data_interval": data.get("data_interval", "1m"),
        "features": features,
        "candles": candles
    }

    if len(_SNAPSHOT_CACHE) > 256:
        _SNAPSHOT_CACHE.pop(next(iter(_SNAPSHOT_CACHE)))
    _SNAPSHOT_CACHE[clean_sym_upper] = {
        "_ts": latest_ts,
        "_price": curr_price,
        "_count": candle_count,
        "snapshot": result.copy()
    }

    return result


def inject_simulation_tick(symbol: str, target_movement_pct: float, momentum_direction: str = "UP") -> Dict[str, Any]:
    """Simulation helper for alert testing."""
    data = get_stock_visualization_data(symbol, time_range="1D")
    return {"status": "ok", "symbol": symbol, "data": data}


def reset_simulation(symbol: Optional[str] = None):
    """Reset simulated ticks."""
    pass


def fetch_intraday_candles(symbol: str, period: str = "5d", interval: str = "1m") -> pd.DataFrame:
    """
    Downloads historical intraday candles for feature engineering and training.
    """
    norm_sym = normalize_ticker(symbol)
    try:
        ticker = _get_yf().Ticker(norm_sym)
        df = ticker.history(period=period, interval=interval, auto_adjust=False, timeout=8)
        if df.empty and period == "5d" and interval == "1m":
            df = ticker.history(period="7d", interval="1m", auto_adjust=False, timeout=8)
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as e:
        print(f"[MarketData] Error fetching intraday candles for {symbol}: {e}")
        return pd.DataFrame()



