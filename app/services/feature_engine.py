import hashlib
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple

# High-performance in-memory calculation caches for deterministic indicator series
_COMPUTE_ST_CACHE: Dict[Tuple, pd.DataFrame] = {}
_ANALYZE_ST_CACHE: Dict[Tuple, Dict[str, Any]] = {}
_REVERSAL_FEAT_CACHE: Dict[Tuple, Tuple] = {}
_MARKET_FEATURES_CACHE: Dict[Tuple, pd.DataFrame] = {}
_LIST_CANDLE_KEY_CACHE: Dict[Tuple, Tuple] = {}


def get_candle_sequence_key(candles_or_df: Any) -> Optional[Tuple]:
    """Generates a deterministic, collision-resistant SHA-256 fingerprint for a candle sequence.
    Inspects all timestamps and OHLCV columns across the entire sequence.
    """
    if candles_or_df is None:
        return None
    try:
        if isinstance(candles_or_df, pd.DataFrame):
            n = len(candles_or_df)
            if n == 0:
                return (0, "empty")
            df = candles_or_df

            # Check if dataframe already has a cached trustworthy fingerprint for this exact shape
            if hasattr(df, "attrs") and "_candle_fingerprint" in df.attrs:
                cached = df.attrs["_candle_fingerprint"]
                if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == n:
                    return cached

            cols_map = {str(c).lower(): c for c in df.columns}
            t_col = cols_map.get("full_time") or cols_map.get("time") or cols_map.get("datetime") or cols_map.get("dt")
            c_col = cols_map.get("close") or cols_map.get("price")
            o_col = cols_map.get("open") or c_col
            h_col = cols_map.get("high") or c_col
            l_col = cols_map.get("low") or c_col
            v_col = cols_map.get("volume") or cols_map.get("raw_volume")

            h = hashlib.sha256()
            h.update(f"{n}:".encode("ascii"))

            if t_col:
                t_vals = df[t_col].astype(str).values
            elif hasattr(df, "index") and not df.index.empty:
                t_vals = df.index.astype(str).values
            else:
                t_vals = np.array([""] * n)

            def to_clean_arr(col, default=0.0):
                if col and col in df.columns:
                    return pd.to_numeric(df[col], errors="coerce").fillna(default).values
                return np.full(n, default, dtype=np.float64)

            o_arr = to_clean_arr(o_col)
            h_arr = to_clean_arr(h_col)
            l_arr = to_clean_arr(l_col)
            c_arr = to_clean_arr(c_col)
            v_arr = to_clean_arr(v_col)

            numeric_matrix = np.column_stack([
                np.round(o_arr, 4),
                np.round(h_arr, 4),
                np.round(l_arr, 4),
                np.round(c_arr, 4),
                np.round(v_arr, 2)
            ]).astype(np.float64)

            h.update(numeric_matrix.tobytes())
            h.update(";".join(t_vals).encode("utf-8", errors="replace"))
            digest = h.hexdigest()
            key = (n, digest)

            if hasattr(df, "attrs"):
                df.attrs["_candle_fingerprint"] = key
            return key

        elif isinstance(candles_or_df, list):
            n = len(candles_or_df)
            if n == 0:
                return (0, "empty")
            
            c0 = candles_or_df[0] if isinstance(candles_or_df[0], dict) else {}
            cn = candles_or_df[-1] if isinstance(candles_or_df[-1], dict) else {}
            cm = candles_or_df[n // 2] if isinstance(candles_or_df[n // 2], dict) else {}
            fast_probe = (
                id(candles_or_df),
                n,
                str(c0.get("time") or c0.get("full_time")),
                str(cn.get("time") or cn.get("full_time")),
                round(float(c0.get("close") or c0.get("price") or 0.0), 2),
                round(float(cn.get("close") or cn.get("price") or 0.0), 2),
                round(float(cm.get("close") or cm.get("price") or 0.0), 2)
            )
            if fast_probe in _LIST_CANDLE_KEY_CACHE:
                return _LIST_CANDLE_KEY_CACHE[fast_probe]

            h = hashlib.sha256()
            h.update(f"{n}:".encode("ascii"))
            parts = []
            for c in candles_or_df:
                if isinstance(c, dict):
                    t = str(c.get("full_time") or c.get("time") or c.get("datetime") or c.get("dt") or "")
                    try: o = round(float(c.get("open") if c.get("open") is not None else (c.get("close") or c.get("price") or 0.0)), 4)
                    except Exception: o = 0.0
                    try: hi = round(float(c.get("high") if c.get("high") is not None else (c.get("close") or c.get("price") or 0.0)), 4)
                    except Exception: hi = 0.0
                    try: lo = round(float(c.get("low") if c.get("low") is not None else (c.get("close") or c.get("price") or 0.0)), 4)
                    except Exception: lo = 0.0
                    try: cl = round(float(c.get("close") if c.get("close") is not None else (c.get("price") or 0.0)), 4)
                    except Exception: cl = 0.0
                    try: v = round(float(c.get("volume") if c.get("volume") is not None else (c.get("raw_volume") or 0.0)), 2)
                    except Exception: v = 0.0
                    parts.append(f"{t}:{o}:{hi}:{lo}:{cl}:{v}")
            h.update(";".join(parts).encode("utf-8", errors="replace"))
            key = (n, h.hexdigest())
            if len(_LIST_CANDLE_KEY_CACHE) > 512:
                _LIST_CANDLE_KEY_CACHE.pop(next(iter(_LIST_CANDLE_KEY_CACHE)))
            _LIST_CANDLE_KEY_CACHE[fast_probe] = key
            return key
    except Exception:
        return None
    return None



def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index (RSI)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    
    # Exponential moving average for wilder smoothing
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    
    rs = avg_gain / (avg_loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50.0)


def compute_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Calculate MACD Line, Signal Line, and MACD Histogram."""
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14
) -> pd.Series:
    """
    Calculate Average True Range (ATR) with standard True Range and Wilder's RMA smoothing:
    TR = max(High - Low, abs(High - Previous Close), abs(Low - Previous Close))
    For the first bar: TR = High - Low
    Wilder's Smoothing:
      Initial ATR = SMA of first 'period' TRs
      ATR[i] = (ATR[i-1] * (period - 1) + TR[i]) / period
    """
    if close is None or len(close) == 0:
        return pd.Series([], dtype=float)

    p = max(1, int(period))
    c = pd.to_numeric(close, errors='coerce').ffill().bfill().fillna(100.0)
    h = pd.to_numeric(high, errors='coerce').fillna(c)
    l = pd.to_numeric(low, errors='coerce').fillna(c)

    # Physical validity constraint: Low > 0, High >= Low
    l = np.maximum(0.01, l)
    h = np.maximum(h, l)

    n = len(c)
    tr = np.zeros(n, dtype=float)
    tr[0] = max(0.01, float(h.iloc[0] - l.iloc[0]))

    if n > 1:
        prev_close = c.shift(1).bfill().values
        h_vals = h.values
        l_vals = l.values

        tr1 = h_vals[1:] - l_vals[1:]
        tr2 = np.abs(h_vals[1:] - prev_close[1:])
        tr3 = np.abs(l_vals[1:] - prev_close[1:])
        tr[1:] = np.maximum(tr1, np.maximum(tr2, tr3))

    atr_vals = np.zeros(n, dtype=float)
    if n < p:
        cumsum = np.cumsum(tr)
        atr_vals = cumsum / np.arange(1, n + 1)
    else:
        cumsum = np.cumsum(tr[:p])
        atr_vals[:p] = cumsum / np.arange(1, p + 1)
        for i in range(p, n):
            atr_vals[i] = (atr_vals[i - 1] * (p - 1) + tr[i]) / p

    return pd.Series(atr_vals, index=close.index, name="atr")


def sanitize_candles(candles_or_df: Any) -> pd.DataFrame:
    """
    Chronologically sorts, deduplicates, and validates OHLCV candle data.
    - Standardizes column names to lowercase.
    - Parses timestamps and sorts chronologically.
    - Deduplicates by candle timestamp (keeps latest update).
    - Enforces physical validity: High >= max(Open, Close), Low <= min(Open, Close), Low > 0.
    """
    if isinstance(candles_or_df, pd.DataFrame):
        df = candles_or_df.copy()
    elif isinstance(candles_or_df, list):
        if not candles_or_df:
            return pd.DataFrame()
        df = pd.DataFrame(candles_or_df)
    else:
        return pd.DataFrame()

    if df.empty:
        return df

    df.columns = [str(c).lower() for c in df.columns]
    if 'close' not in df.columns and 'price' in df.columns:
        df['close'] = df['price']

    # Identify timestamp column
    time_col = None
    for col in ('full_time', 'timestamp', 'datetime', 'time', 'date'):
        if col in df.columns:
            time_col = col
            break

    if time_col:
        try:
            s_time = df[time_col]
            needs_sort_or_dedup = True
            # Fast-path: skip expensive datetime parsing if already monotonic unique sequence
            if s_time.is_monotonic_increasing and not s_time.hasnans and s_time.is_unique:
                needs_sort_or_dedup = False

            if needs_sort_or_dedup:
                try:
                    sort_ts = pd.to_datetime(s_time, errors='coerce', format='ISO8601')
                    if sort_ts.isna().all() and not s_time.isna().all():
                        sort_ts = pd.to_datetime(s_time, errors='coerce', format='mixed')
                except Exception:
                    sort_ts = pd.to_datetime(s_time, errors='coerce', format='mixed')
                df['_sort_ts'] = sort_ts
                df = df.sort_values(by=['_sort_ts'], ascending=True)
                df = df.drop_duplicates(subset=[time_col], keep='last')
                df = df.drop(columns=['_sort_ts'])
                df = df.reset_index(drop=True)
        except Exception:
            pass

    df = df.reset_index(drop=True)

    # Vectorized physical OHLC sanitization and boundary enforcement
    close_raw = pd.to_numeric(df.get('close', 100.0), errors='coerce')
    close = close_raw.mask(close_raw <= 0.0, np.nan).ffill().bfill().fillna(100.0).to_numpy()
    close = np.maximum(0.01, close)

    open_raw = pd.to_numeric(df.get('open', close), errors='coerce')
    open_p = open_raw.mask(open_raw <= 0.0, np.nan).fillna(pd.Series(close, index=open_raw.index)).to_numpy()
    open_p = np.maximum(0.01, open_p)

    high_raw = pd.to_numeric(df.get('high', close), errors='coerce')
    high = high_raw.mask(high_raw <= 0.0, np.nan).fillna(pd.Series(close, index=high_raw.index)).to_numpy()
    high = np.maximum(0.01, high)

    low_raw = pd.to_numeric(df.get('low', close), errors='coerce')
    low = low_raw.mask(low_raw <= 0.0, np.nan).fillna(pd.Series(close, index=low_raw.index)).to_numpy()
    low = np.maximum(0.01, low)

    valid_low = np.maximum(0.01, np.minimum(low, np.minimum(open_p, close)))
    valid_high = np.maximum(valid_low, np.maximum(high, np.maximum(open_p, close)))

    df['open'] = np.round(open_p, 2)
    df['high'] = np.round(valid_high, 2)
    df['low'] = np.round(valid_low, 2)
    df['close'] = np.round(close, 2)
    return df


def compute_supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0,
    band_buffer_pct: float = 0.0
) -> pd.DataFrame:
    """
    Computes standard SuperTrend indicator with ATR volatility bands.
    - True Range & Wilder's Smoothing (RMA) ATR.
    - Basic Bands: HL2 +/- Multiplier * ATR.
    - Recursive Final Bands:
        Final Upper: lowered or reset if close breaks above previous upper.
        Final Lower: raised or reset if close breaks below previous lower.
    - Trend Switching:
        Flips to BEARISH (-1 / SELL) when close drops below Final Lower.
        Flips to BULLISH (1 / BUY) when close climbs above Final Upper.
    - Configurable optional band_buffer_pct for hysteresis filtering.
    """
    if df.empty:
        return df

    ckey = get_candle_sequence_key(df)
    cache_key = (ckey, int(period), float(multiplier), float(band_buffer_pct)) if ckey is not None else None
    if cache_key is not None and cache_key in _COMPUTE_ST_CACHE:
        return _COMPUTE_ST_CACHE[cache_key].copy()

    data = df.copy()

    data.columns = [c.lower() for c in data.columns]
    if 'close' not in data.columns and 'price' in data.columns:
        data['close'] = data['price']
    if 'high' not in data.columns:
        data['high'] = data.get('close', 100.0)
    if 'low' not in data.columns:
        data['low'] = data.get('close', 100.0)

    n = len(data)
    if n < 2:
        close_val = pd.to_numeric(data.get('close', 100.0), errors='coerce').fillna(100.0)
        data['supertrend'] = close_val
        data['supertrend_direction'] = 1
        data['supertrend_signal'] = None
        data['supertrend_upper'] = close_val
        data['supertrend_lower'] = close_val
        data['atr'] = 1.0
        return data

    close = pd.to_numeric(data['close'], errors='coerce').ffill().bfill().fillna(100.0)
    open_p = pd.to_numeric(data.get('open', close), errors='coerce').fillna(close)
    high = pd.to_numeric(data['high'], errors='coerce').fillna(close)
    low = pd.to_numeric(data['low'], errors='coerce').fillna(close)

    # Physical validity constraints
    low = np.maximum(0.01, np.minimum(low, np.minimum(open_p, close)))
    high = np.maximum(high, np.maximum(open_p, close))
    low = np.minimum(low, high)

    atr = compute_atr(high, low, close, period=period)
    hl2 = (high + low) / 2.0
    basic_upper = hl2 + (float(multiplier) * atr)
    basic_lower = np.maximum(0.01, hl2 - (float(multiplier) * atr))

    # Fast contiguous array extraction to avoid pandas iloc indexing overhead in loop
    b_up_arr = np.maximum(0.01, basic_upper.to_numpy(dtype=np.float64))
    b_low_arr = np.maximum(0.01, basic_lower.to_numpy(dtype=np.float64))
    close_arr = close.to_numpy(dtype=np.float64)
    atr_arr = atr.to_numpy(dtype=np.float64)

    final_upper = np.zeros(n, dtype=np.float64)
    final_lower = np.zeros(n, dtype=np.float64)
    trend = np.ones(n, dtype=np.int32)
    supertrend = np.zeros(n, dtype=np.float64)
    signal = [None] * n

    # Initialize first candle with positive bounds
    final_upper[0] = b_up_arr[0]
    final_lower[0] = b_low_arr[0]
    trend[0] = 1 if close_arr[0] >= final_lower[0] else -1
    supertrend[0] = final_lower[0] if trend[0] == 1 else final_upper[0]

    buf_factor = (float(band_buffer_pct) / 100.0) if band_buffer_pct > 0 else 0.0

    for i in range(1, n):
        b_up = b_up_arr[i]
        b_low = b_low_arr[i]
        c_prev = close_arr[i - 1]
        c_curr = close_arr[i]

        # Final Lower Band (Support):
        # Basic lower band is raised or reset on breakdown
        if b_low > final_lower[i - 1] or c_prev < final_lower[i - 1]:
            final_lower[i] = b_low
        else:
            final_lower[i] = final_lower[i - 1]

        # Final Upper Band (Resistance):
        # Basic upper band is lowered or reset on breakout
        if b_up < final_upper[i - 1] or c_prev > final_upper[i - 1]:
            final_upper[i] = b_up
        else:
            final_upper[i] = final_upper[i - 1]

        # Trend direction determination with optional hysteresis buffer
        prev_trend = trend[i - 1]
        buf = (atr_arr[i] * buf_factor) if buf_factor > 0 else 0.0

        if prev_trend == 1:
            if c_curr < (final_lower[i] - buf):
                trend[i] = -1
                supertrend[i] = final_upper[i]
                signal[i] = "SELL"
            else:
                trend[i] = 1
                supertrend[i] = final_lower[i]
                signal[i] = None
        else:
            if c_curr > (final_upper[i] + buf):
                trend[i] = 1
                supertrend[i] = final_lower[i]
                signal[i] = "BUY"
            else:
                trend[i] = -1
                supertrend[i] = final_upper[i]
                signal[i] = None

    data['supertrend'] = np.round(supertrend, 2)
    data['supertrend_direction'] = trend.tolist()
    data['supertrend_signal'] = signal
    data['supertrend_upper'] = np.round(final_upper, 2)
    data['supertrend_lower'] = np.round(final_lower, 2)
    data['atr'] = np.round(atr_arr, 2)

    if cache_key is not None:
        if len(_COMPUTE_ST_CACHE) > 256:
            _COMPUTE_ST_CACHE.pop(next(iter(_COMPUTE_ST_CACHE)))
        _COMPUTE_ST_CACHE[cache_key] = data.copy()

    return data



def calculate_reversal_risk_score(
    distance_points: float,
    atr_value: float,
    proximity_points: float,
    target_direction: str,
    candle_color: str,
    candle_points: float = 0.0,
    momentum_adverse: float = 0.0,
    delta_rsi: float = 0.0,
    trend_duration_bars: int = 0
) -> float:
    """
    Calculates a calibrated, transparent rule-based reversal-risk score (0.0 to 1.0).
    Not an unverified AI claim — clearly combines:
    1. Distance-to-band normalized by ATR and proximity limit (up to 0.40)
    2. Candle color & momentum alignment with reversal target (up to 0.25)
    3. Multi-bar adverse momentum & RSI divergence towards reversal (up to 0.20)
    4. Trend maturity / duration fatigue (up to 0.15)
    """
    safe_atr = max(0.2, float(atr_value))
    safe_prox = max(0.1, float(proximity_points))
    dist = max(0.0, float(distance_points))

    # 1. Proximity & ATR compression score (0.0 to 0.40): closer to line = higher score
    atr_norm = dist / safe_atr
    prox_ratio = dist / safe_prox
    prox_score = max(0.0, min(0.25, 0.25 * (1.0 - min(1.0, prox_ratio / 2.0))))
    atr_score = max(0.0, min(0.15, 0.15 * (1.0 - min(1.0, atr_norm))))

    # 2. Candle color & single-bar momentum alignment (0.05 to 0.25)
    mom_score = 0.10
    tgt_upper = str(target_direction).upper()
    col_upper = str(candle_color).upper()
    if tgt_upper == "BUY" and col_upper == "GREEN":
        mom_score = 0.25
    elif tgt_upper == "SELL" and col_upper == "RED":
        mom_score = 0.25
    elif (tgt_upper == "BUY" and col_upper == "RED") or (tgt_upper == "SELL" and col_upper == "GREEN"):
        mom_score = 0.05

    # 3. Multi-bar adverse momentum & RSI divergence (0.0 to 0.20)
    adverse_score = 0.0
    if momentum_adverse > 0:
        # Momentum is pushing into the Supertrend band
        adverse_ratio = min(1.0, float(momentum_adverse) / safe_atr)
        adverse_score += 0.12 * adverse_ratio
    
    # RSI weakening: if target is SELL, falling RSI is adverse to the existing BUY trend
    if tgt_upper == "SELL" and delta_rsi < -2.0:
        adverse_score += min(0.08, abs(delta_rsi) / 50.0)
    elif tgt_upper == "BUY" and delta_rsi > 2.0:
        adverse_score += min(0.08, delta_rsi / 50.0)

    # 4. Trend duration fatigue (0.0 to 0.15)
    # Trends that have run for >20-40 bars have higher probability of mean-reverting
    fatigue_score = 0.0
    if trend_duration_bars >= 15:
        fatigue_score = min(0.15, (trend_duration_bars - 15) * 0.005)

    total = prox_score + atr_score + mom_score + adverse_score + fatigue_score
    return round(float(np.clip(total, 0.10, 0.95)), 2)


def get_interval_timedelta(interval: str = "1m") -> timedelta:
    inv = (interval or "1m").lower().strip()
    if inv.endswith("m"):
        try:
            return timedelta(minutes=int(inv[:-1]))
        except ValueError:
            return timedelta(minutes=1)
    elif inv.endswith("h"):
        try:
            return timedelta(hours=int(inv[:-1]))
        except ValueError:
            return timedelta(hours=1)
    elif inv.endswith("d"):
        try:
            return timedelta(days=int(inv[:-1]))
        except ValueError:
            return timedelta(days=1)
    return timedelta(minutes=1)


def parse_candle_timestamp(ts_val: Any) -> Optional[datetime]:
    if ts_val is None:
        return None
    if isinstance(ts_val, datetime):
        return ts_val
    if isinstance(ts_val, pd.Timestamp):
        return ts_val.to_pydatetime()
    s = str(ts_val).strip()
    if not s:
        return None
    # Try ISO format
    try:
        return datetime.fromisoformat(s)
    except Exception:
        pass
    # Try time formats with current date
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p"):
        try:
            t = datetime.strptime(s, fmt).time()
            return datetime.combine(datetime.now().date(), t)
        except Exception:
            pass
    # Try pandas to_datetime
    try:
        pdt = pd.to_datetime(s)
        if hasattr(pdt, "to_pydatetime"):
            return pdt.to_pydatetime()
    except Exception:
        pass
    return None


def is_candle_complete(
    candle: Any,
    interval: str = "1m",
    now: Optional[datetime] = None,
    is_market_open: Optional[bool] = None
) -> bool:
    """
    Determines whether a candle is fully completed based on its timestamp,
    the timeframe interval, and market status.
    """
    if is_market_open is False:
        return True

    delta = get_interval_timedelta(interval)

    ts = None
    if isinstance(candle, dict):
        ts = candle.get("full_time") or candle.get("time") or candle.get("dt") or candle.get("timestamp")
    elif isinstance(candle, pd.Series):
        ts = candle.get("full_time") if "full_time" in candle else (candle.get("time") if "time" in candle else None)
    elif hasattr(candle, "get"):
        ts = candle.get("full_time") or candle.get("time")

    cand_dt = parse_candle_timestamp(ts)
    if cand_dt is None:
        return True

    ref_now = now
    if ref_now is None:
        ref_now = datetime.now(cand_dt.tzinfo) if cand_dt.tzinfo else datetime.now()

    if cand_dt.tzinfo is not None and ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=cand_dt.tzinfo)
    elif cand_dt.tzinfo is None and ref_now.tzinfo is not None:
        cand_dt = cand_dt.replace(tzinfo=ref_now.tzinfo)

    return ref_now >= (cand_dt + delta)


def analyze_supertrend_hit(
    candles_or_df: Any,
    current_price: Optional[float] = None,
    previous_direction: Optional[str] = None,
    period: int = 10,
    multiplier: float = 3.0,
    proximity_points: Optional[float] = None,
    proximity_percent: Optional[float] = None,
    band_buffer_pct: float = 0.0,
    min_atr_distance_filter: float = 0.0,
    candle_interval: str = "1m",
    now: Optional[datetime] = None,
    is_market_open: Optional[bool] = None,
    data_status: Optional[str] = None
) -> Dict[str, Any]:
    """
    Standard, reliable SuperTrend calculation and hit/proximity detector:
    - Sorts chronologically, deduplicates, and sanitizes candle OHLC.
    - Preserves historical candle OHLC without overwriting with live ticks.
    - Determines completed candles based on candle timestamp, timeframe, and market status.
    - Computes confirmed Supertrend direction strictly using completed candles.
    - Uses live/developing prices strictly for provisional analysis and intrabar cross detection.
    - Reversal risk scoring based on proximity, momentum, and regime.
    - Supports percentage-based prediction distance:
      P_ref = current live price
      Upper boundary = P_ref * (1 + p / 100)
      Lower boundary = P_ref * (1 - p / 100)
    """
    clean_df = sanitize_candles(candles_or_df)
    ref_p = float(current_price or 100.0)

    if proximity_percent is not None:
        prox_pct = max(0.01, float(proximity_percent))
        prox_limit = round(ref_p * (prox_pct / 100.0), 2)
    elif proximity_points is not None:
        prox_limit = max(0.01, float(proximity_points))
        prox_pct = round((prox_limit / max(0.01, ref_p)) * 100.0, 4)
    else:
        prox_pct = 0.50
        prox_limit = round(ref_p * (prox_pct / 100.0), 2)

    if clean_df.empty or len(clean_df) < 2:
        p = ref_p
        return {
            "current_price": p,
            "supertrend_price": p,
            "direction": "BUY",
            "previous_direction": previous_direction or "BUY",
            "target_direction": "SELL",
            "distance_points": 0.0,
            "distance_percent": 0.0,
            "proximity_percent": round(prox_pct, 2),
            "proximity_threshold": prox_limit,
            "permitted_distance_points": prox_limit,
            "upper_boundary": round(p * (1.0 + prox_pct / 100.0), 2),
            "lower_boundary": round(p * (1.0 - prox_pct / 100.0), 2),
            "reference_price": round(p, 2),
            "is_approaching": False,
            "is_prediction_ready": False,
            "is_flipped": False,
            "is_confirmed_flip": False,
            "is_intrabar_cross": False,
            "flip_signal": None,
            "reversal_risk_score": 0.0,
            "atr": 1.0,
            "atr_normalized_distance": 0.0,
            "penetration_atr_ratio": 0.0,
            "provisional_direction": previous_direction or "BUY",
            "provisional_supertrend_price": p,
            "candle_open": p,
            "candle_close": p,
            "candle_points": 0.0,
            "candle_color": "NEUTRAL",
            "candle_body_size": 0.0,
            "is_candle_stable": False,
            "candle_matches_target": False,
            "candle_age_seconds": 0.0,
            "prev_candle_points": 0.0,
            "prev_candle_color": "GREEN",
            "candle_timestamp": "",
            "developing_candle_timestamp": "",
            "series": []
        }

    ckey = get_candle_sequence_key(clean_df)
    now_round = now.strftime("%Y-%m-%d %H:%M") if now else None
    cache_key = (
        ckey,
        round(ref_p, 2),
        previous_direction,
        int(period),
        float(multiplier),
        round(prox_pct, 4),
        round(prox_limit, 2),
        float(band_buffer_pct),
        float(min_atr_distance_filter),
        candle_interval,
        is_market_open,
        data_status,
        now_round
    ) if ckey is not None else None

    if cache_key is not None and cache_key in _ANALYZE_ST_CACHE:
        return _ANALYZE_ST_CACHE[cache_key].copy()

    df = clean_df.copy()
    cp = float(current_price if current_price is not None else df.iloc[-1]['close'])

    last_row = df.iloc[-1]

    # Determine completion of latest candle without blindly assuming iloc[-2]
    if is_market_open is False or data_status == "CLOSED":
        last_is_complete = True
    else:
        ts_val = last_row.get("full_time") or last_row.get("time") or last_row.get("dt")
        parsed_dt = parse_candle_timestamp(ts_val)
        if parsed_dt is not None:
            last_is_complete = is_candle_complete(last_row, interval=candle_interval, now=now, is_market_open=is_market_open)
        else:
            if current_price is not None and abs(cp - float(last_row['close'])) > 1e-4:
                last_is_complete = False
            else:
                last_is_complete = True

    if last_is_complete:
        completed_df = df
        developing_row = None
    else:
        if len(df) >= 2:
            completed_df = df.iloc[:-1]
            developing_row = df.iloc[-1]
        else:
            completed_df = df
            developing_row = df.iloc[-1]

    # Compute confirmed Supertrend strictly on completed candles
    completed_st_df = compute_supertrend(completed_df, period=period, multiplier=multiplier, band_buffer_pct=band_buffer_pct)
    last_completed_row = completed_st_df.iloc[-1]
    completed_ts_str = str(last_completed_row.get('full_time') or last_completed_row.get('time') or '')
    raw_completed_dir_code = int(last_completed_row['supertrend_direction'])
    raw_completed_dir = "BUY" if raw_completed_dir_code == 1 else "SELL"
    raw_completed_st_p = float(last_completed_row['supertrend'])
    raw_completed_signal = last_completed_row.get('supertrend_signal')
    completed_atr = float(last_completed_row.get('atr', 1.0))
    completed_close = float(last_completed_row.get('close', cp))

    # The confirmed Supertrend indicator values are strictly derived from completed candles
    confirmed_dir = raw_completed_dir
    confirmed_st_p = raw_completed_st_p
    confirmed_signal = raw_completed_signal

    target_dir = "SELL" if confirmed_dir == "BUY" else "BUY"
    distance_points = round(abs(cp - confirmed_st_p), 2)
    atr_norm_dist = round(distance_points / max(0.01, completed_atr), 2)

    # Detect raw completed trend reversal
    resolved_prev_dir = previous_direction if previous_direction in ("BUY", "SELL") else None
    if data_status in ("STALE", "DATA_UNAVAILABLE", "ERROR"):
        is_confirmed_flip = False
    elif resolved_prev_dir is not None:
        is_confirmed_flip = bool(confirmed_dir != resolved_prev_dir and confirmed_st_p > 0.01)
    else:
        is_confirmed_flip = bool(confirmed_signal in ("BUY", "SELL"))

    # Reversal penetration quality metrics on completed bar
    if confirmed_dir == "SELL":
        penetration_distance = max(0.0, confirmed_st_p - completed_close)
    else:
        penetration_distance = max(0.0, completed_close - confirmed_st_p)
    penetration_atr_ratio = round(penetration_distance / max(0.01, completed_atr), 2)

    # Intrabar penetration during currently active/forming developing candle
    is_intrabar_cross = False
    if confirmed_dir == "BUY" and cp < confirmed_st_p:
        is_intrabar_cross = True
    elif confirmed_dir == "SELL" and cp > confirmed_st_p:
        is_intrabar_cross = True

    # Provisional state (strictly provisional, NEVER overwrites confirmed trend)
    provisional_dir = ("SELL" if confirmed_dir == "BUY" else "BUY") if is_intrabar_cross else confirmed_dir
    provisional_st_p = confirmed_st_p

    is_flipped = is_confirmed_flip or is_intrabar_cross
    flip_signal = ("SELL" if confirmed_dir == "BUY" else "BUY") if is_intrabar_cross else (confirmed_dir if is_confirmed_flip else None)

    # Early Warning: live price enters configured percentage proximity zone
    # Reference price convention: P_ref = cp
    # Lower boundary = cp * (1 - prox_pct / 100), Upper boundary = cp * (1 + prox_pct / 100)
    distance_percent = round((distance_points / max(0.01, cp)) * 100.0, 4)
    upper_boundary = round(cp * (1.0 + prox_pct / 100.0), 2)
    lower_boundary = round(cp * (1.0 - prox_pct / 100.0), 2)

    if data_status in ("STALE", "DATA_UNAVAILABLE", "ERROR"):
        is_approaching = False
        is_prediction_ready = False
    else:
        # BUY trend: reversal occurs if price drops to ST (downside distance <= permitted movement)
        # SELL trend: reversal occurs if price rises to ST (upside distance <= permitted movement)
        is_within_pct = bool(distance_percent <= prox_pct or distance_points <= prox_limit or is_intrabar_cross)
        is_approaching = bool(is_within_pct and not is_confirmed_flip and confirmed_st_p > 0.01)
        if min_atr_distance_filter > 0 and is_approaching:
            is_approaching = bool(distance_points <= (min_atr_distance_filter * completed_atr))
        is_prediction_ready = is_approaching

    c_open = float(developing_row.get('open', last_completed_row.get('open', cp))) if developing_row is not None else float(last_completed_row.get('open', cp))
    c_close = cp
    candle_points = round(c_close - c_open, 2)
    candle_color = "GREEN" if candle_points >= 0 else "RED"
    candle_body_abs = round(abs(candle_points), 2)

    # Calculate adverse momentum, delta RSI, and trend duration for risk scoring
    trend_dur_bars = 1
    if len(completed_st_df) >= 2:
        rev_dirs = completed_st_df['supertrend_direction'].values
        for d_idx in range(len(rev_dirs) - 1, -1, -1):
            if rev_dirs[d_idx] == raw_completed_dir_code:
                trend_dur_bars += 1
            else:
                break

    mom_adv = 0.0
    if len(completed_st_df) >= 4:
        past_c = float(completed_st_df.iloc[-4]['close'])
        if confirmed_dir == "BUY":
            mom_adv = max(0.0, past_c - cp)
        else:
            mom_adv = max(0.0, cp - past_c)

    delta_rsi_val = 0.0
    if len(completed_st_df) >= 5:
        rsi_series = compute_rsi(completed_st_df['close'], period=14)
        delta_rsi_val = float(rsi_series.iloc[-1] - rsi_series.iloc[-4])

    risk_score = calculate_reversal_risk_score(
        distance_points=distance_points,
        atr_value=completed_atr,
        proximity_points=prox_limit,
        target_direction=target_dir,
        candle_color=candle_color,
        candle_points=candle_points,
        momentum_adverse=mom_adv,
        delta_rsi=delta_rsi_val,
        trend_duration_bars=trend_dur_bars
    )

    # Vectorized conversion of series_points instead of slow iterrows()
    t_vals = completed_st_df['full_time'].values if 'full_time' in completed_st_df.columns else (completed_st_df['time'].values if 'time' in completed_st_df.columns else completed_st_df.index.values)
    raw_prices = completed_st_df['close'].values if 'close' in completed_st_df.columns else (completed_st_df['price'].values if 'price' in completed_st_df.columns else [cp] * len(completed_st_df))
    raw_sts = completed_st_df['supertrend'].values
    st_dirs = completed_st_df['supertrend_direction'].values
    raw_sigs = completed_st_df['supertrend_signal'].values if 'supertrend_signal' in completed_st_df.columns else [None] * len(completed_st_df)

    series_points = []
    for t_val, raw_price, raw_st, st_dir, raw_sig in zip(t_vals, raw_prices, raw_sts, st_dirs, raw_sigs):
        p_val = round(float(raw_price), 2) if (raw_price is not None and not np.isnan(raw_price)) else round(cp, 2)
        st_val = round(float(raw_st), 2) if (raw_st is not None and not np.isnan(raw_st)) else round(cp, 2)
        sig_clean = str(raw_sig) if (raw_sig is not None and str(raw_sig) in ("BUY", "SELL")) else None

        series_points.append({
            "time": str(t_val),
            "price": p_val,
            "supertrend": max(0.01, st_val),
            "direction": "BUY" if st_dir == 1 else "SELL",
            "signal": sig_clean
        })

    if developing_row is not None:
        dev_t = developing_row.get('time') or developing_row.get('full_time') or "live"
        series_points.append({
            "time": str(dev_t),
            "price": round(cp, 2),
            "supertrend": max(0.01, round(provisional_st_p, 2)),
            "direction": provisional_dir,
            "signal": None
        })

    dev_ts_str = str(developing_row.get('full_time') or developing_row.get('time') or '') if developing_row is not None else completed_ts_str

    res = {
        "current_price": round(cp, 2),
        "supertrend_price": round(confirmed_st_p, 2),
        "direction": confirmed_dir,
        "previous_direction": resolved_prev_dir or confirmed_dir,
        "target_direction": target_dir,
        "distance_points": distance_points,
        "distance_percent": distance_percent,
        "proximity_percent": round(prox_pct, 2),
        "proximity_threshold": prox_limit,
        "permitted_distance_points": prox_limit,
        "upper_boundary": upper_boundary,
        "lower_boundary": lower_boundary,
        "reference_price": round(cp, 2),
        "is_approaching": is_approaching,
        "is_prediction_ready": is_approaching,
        "is_flipped": is_flipped,
        "is_confirmed_flip": is_confirmed_flip,
        "is_intrabar_cross": is_intrabar_cross,
        "flip_signal": flip_signal,
        "reversal_risk_score": risk_score,
        "atr": round(completed_atr, 2),
        "atr_normalized_distance": atr_norm_dist,
        "penetration_atr_ratio": penetration_atr_ratio,
        "provisional_direction": provisional_dir,
        "provisional_supertrend_price": round(provisional_st_p, 2),
        "candle_open": round(c_open, 2),
        "candle_close": round(c_close, 2),
        "candle_points": candle_points,
        "candle_color": candle_color,
        "candle_body_size": candle_body_abs,
        "is_candle_stable": True,
        "candle_matches_target": True,
        "candle_age_seconds": 60.0,
        "prev_candle_points": 0.0,
        "prev_candle_color": "GREEN",
        "candle_timestamp": completed_ts_str,
        "developing_candle_timestamp": dev_ts_str,
        "series": series_points
    }

    if cache_key is not None:
        if len(_ANALYZE_ST_CACHE) > 256:
            _ANALYZE_ST_CACHE.pop(next(iter(_ANALYZE_ST_CACHE)))
        _ANALYZE_ST_CACHE[cache_key] = res.copy()

    return res




def compute_market_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes rich intraday technical features from OHLCV 1-minute candle data.
    Input df must contain: ['open', 'high', 'low', 'close', 'volume'].
    Returns df with all computed features.
    """
    if df.empty or len(df) < 5:
        return df

    ckey = get_candle_sequence_key(df)
    if ckey is not None and ckey in _MARKET_FEATURES_CACHE:
        return _MARKET_FEATURES_CACHE[ckey].copy()

    data = df.copy()
    # Normalize column names to lowercase
    data.columns = [c.lower() for c in data.columns]
    
    # Ensure close/price exists
    if 'close' not in data.columns and 'price' in data.columns:
        data['close'] = data['price']
    elif 'price' not in data.columns and 'close' in data.columns:
        data['price'] = data['close']
        
    if 'close' not in data.columns:
        return df

    # Fallback missing OHLCV columns
    if 'open' not in data.columns:
        data['open'] = data['close']
    if 'high' not in data.columns:
        data['high'] = data['close']
    if 'low' not in data.columns:
        data['low'] = data['close']
    if 'volume' not in data.columns:
        data['volume'] = 0.0
    
    close = data['close']
    high = data['high']
    low = data['low']
    volume = data['volume']
    
    # 1. Short-term Returns (%)
    data['return_1m'] = close.pct_change(1) * 100
    data['return_3m'] = close.pct_change(3) * 100
    data['return_5m'] = close.pct_change(5) * 100
    data['return_10m'] = close.pct_change(10) * 100
    data['return_15m'] = close.pct_change(15) * 100
    
    # 2. Momentum & Rate of Change (ROC)
    data['momentum_5m'] = close - close.shift(5)
    data['momentum_10m'] = close - close.shift(10)
    data['roc_5m'] = ((close - close.shift(5)) / (close.shift(5) + 1e-9)) * 100
    data['roc_10m'] = ((close - close.shift(10)) / (close.shift(10) + 1e-9)) * 100
    
    # 3. Moving Averages (SMA & EMA)
    data['sma_5'] = close.rolling(window=5, min_periods=1).mean()
    data['sma_15'] = close.rolling(window=15, min_periods=1).mean()
    data['sma_30'] = close.rolling(window=30, min_periods=1).mean()
    data['ema_9'] = close.ewm(span=9, adjust=False).mean()
    data['ema_21'] = close.ewm(span=21, adjust=False).mean()
    
    # Distance from Moving Averages (%)
    data['dist_ema_9'] = ((close - data['ema_9']) / (data['ema_9'] + 1e-9)) * 100
    data['dist_ema_21'] = ((close - data['ema_21']) / (data['ema_21'] + 1e-9)) * 100
    
    # 4. Relative Strength Index (RSI)
    data['rsi_14'] = compute_rsi(close, period=14)
    
    # 5. MACD
    macd, signal, hist = compute_macd(close, fast=12, slow=26, signal=9)
    data['macd'] = macd
    data['macd_signal'] = signal
    data['macd_hist'] = hist
    
    # 6. Volatility & Average True Range (ATR)
    atr = compute_atr(high, low, close, period=14)
    data['atr_14'] = atr
    data['natr_14'] = (atr / (close + 1e-9)) * 100  # Normalized ATR %
    data['volatility_10m'] = data['return_1m'].rolling(window=10, min_periods=2).std().fillna(0.0)
    data['volatility_30m'] = data['return_1m'].rolling(window=30, min_periods=2).std().fillna(0.0)
    
    # 7. Volume Indicators
    vol_sma_20 = volume.rolling(window=20, min_periods=1).mean()
    data['vol_change_1m'] = volume.pct_change(1) * 100
    data['vol_change_5m'] = volume.pct_change(5) * 100
    data['vol_ratio_20m'] = volume / (vol_sma_20 + 1e-9)
    
    # 8. High-Low Range
    data['hl_range_pct'] = ((high - low) / (close + 1e-9)) * 100
    
    # 9. Intraday Cumulative Return
    # From the start of the available intraday session window
    data['intraday_cum_return'] = ((close - data['open'].iloc[0]) / (data['open'].iloc[0] + 1e-9)) * 100
    
    # 10. SuperTrend Indicator
    try:
        st_res = compute_supertrend(data, period=10, multiplier=3.0)
        data['supertrend'] = st_res['supertrend']
        data['supertrend_direction'] = st_res['supertrend_direction']
    except Exception:
        data['supertrend'] = close
        data['supertrend_direction'] = 1

    # Clean any remaining NaNs or Infs
    data = data.replace([np.inf, -np.inf], 0.0).fillna(0.0)
    
    if ckey is not None:
        if len(_MARKET_FEATURES_CACHE) > 256:
            _MARKET_FEATURES_CACHE.pop(next(iter(_MARKET_FEATURES_CACHE)))
        _MARKET_FEATURES_CACHE[ckey] = data.copy()

    return data


def create_dynamic_features_vector(
    market_features: Dict[str, float],
    target_percent: float,
    warning_minutes: int,
    direction: str  # 'UP' or 'DOWN'
) -> np.ndarray:
    """
    Creates the unified feature vector combining real-time market features
    with user-configured dynamic parameters (target %, warning window, direction).
    Matches the exact training feature order.
    """
    is_up = 1.0 if direction.upper() == "UP" else 0.0
    dir_sign = 1.0 if is_up == 1.0 else -1.0
    
    # Required velocity (% per minute needed to hit target)
    req_velocity = target_percent / max(float(warning_minutes), 1.0)
    
    # Volatility buffer (Z-score difficulty: how many std deviations needed)
    vol_10 = market_features.get('volatility_10m', 0.1)
    if vol_10 <= 0.001:
        vol_10 = 0.1
    horizon_sqrt = np.sqrt(max(float(warning_minutes), 1.0))
    vol_zscore = target_percent / (vol_10 * horizon_sqrt + 1e-6)
    
    # Direction-aligned momentum
    mom_5 = market_features.get('momentum_5m', 0.0) * dir_sign
    roc_5 = market_features.get('roc_5m', 0.0) * dir_sign
    roc_10 = market_features.get('roc_10m', 0.0) * dir_sign
    
    # RSI alignment: for UP, higher RSI is bullish unless overbought; for DOWN, lower RSI is bearish
    rsi = market_features.get('rsi_14', 50.0)
    rsi_factor = (rsi - 50.0) * dir_sign
    
    features = [
        # User Configuration
        target_percent,
        float(warning_minutes),
        is_up,
        req_velocity,
        vol_zscore,
        
        # Returns & Momentum
        market_features.get('return_1m', 0.0),
        market_features.get('return_3m', 0.0),
        market_features.get('return_5m', 0.0),
        market_features.get('return_10m', 0.0),
        mom_5,
        roc_5,
        roc_10,
        
        # Technicals
        market_features.get('dist_ema_9', 0.0),
        market_features.get('dist_ema_21', 0.0),
        rsi,
        rsi_factor,
        market_features.get('macd', 0.0),
        market_features.get('macd_signal', 0.0),
        market_features.get('macd_hist', 0.0) * dir_sign,
        
        # Volatility & Volume
        market_features.get('natr_14', 0.0),
        vol_10,
        market_features.get('volatility_30m', 0.1),
        market_features.get('vol_ratio_20m', 1.0),
        market_features.get('hl_range_pct', 0.0),
        market_features.get('intraday_cum_return', 0.0)
    ]
    
    return np.array(features, dtype=np.float32)


FEATURE_NAMES = [
    'target_percent',
    'warning_minutes',
    'is_up',
    'req_velocity',
    'vol_zscore',
    'return_1m',
    'return_3m',
    'return_5m',
    'return_10m',
    'mom_5_aligned',
    'roc_5_aligned',
    'roc_10_aligned',
    'dist_ema_9',
    'dist_ema_21',
    'rsi_14',
    'rsi_factor',
    'macd',
    'macd_signal',
    'macd_hist_aligned',
    'natr_14',
    'volatility_10m',
    'volatility_30m',
    'vol_ratio_20m',
    'hl_range_pct',
    'intraday_cum_return'
]


# =====================================================================
# SUPERTREND EARLY REVERSAL PREDICTION FEATURE ENGINE
# =====================================================================

SUPERTREND_REVERSAL_FEATURE_NAMES = [
    'direction_code',        # Current confirmed direction: +1 (BUY) or -1 (SELL)
    'distance_to_band_atr',  # |Close - Supertrend| / ATR
    'band_slope_3m',         # Supertrend band slope: (ST[t] - ST[t-3]) / ATR
    'dist_change_3m',        # Recent band distance change: dist_atr[t] - dist_atr[t-3]
    'return_1m',             # 1-minute return %
    'return_2m',             # 2-minute return %
    'return_3m',             # 3-minute return %
    'return_5m',             # 5-minute return %
    'momentum_3m',           # Raw 3-bar price diff
    'momentum_5m',           # Raw 5-bar price diff
    'mom_adverse_3m',        # Direction-aligned adverse momentum / ATR
    'mom_adverse_5m',        # Direction-aligned adverse momentum / ATR
    'rsi_14',                # Wilder's 14-period RSI
    'delta_rsi_3m',          # 3-bar change in RSI
    'delta_rsi_5m',          # 5-bar change in RSI
    'macd_hist',             # MACD Histogram
    'delta_macd_hist_3m',    # 3-bar change in MACD histogram
    'ema_9_slope_3m',        # 3-bar EMA 9 slope / ATR
    'ema_21_slope_3m',       # 3-bar EMA 21 slope / ATR
    'atr_ratio_5m',          # ATR surge: ATR[t] / ATR[t-5]
    'volatility_10m',        # Rolling std of 1m returns
    'candle_range_atr',      # Current candle (High - Low) / ATR
    'candle_body_atr',       # Current candle (Close - Open) / ATR
    'vol_ratio_20m',         # Volume / 20-period Volume SMA
    'vol_change_1m',         # 1-minute volume % change
    'trend_duration_bars',   # Completed bars in current confirmed trend
    'mtf_5m_trend',          # Multi-timeframe 5m Supertrend trend direction (+1 or -1)
    'mtf_5m_rsi',            # Multi-timeframe 5m RSI context
    'mtf_5m_return',         # Multi-timeframe 5m return %
    'horizon_minutes'        # Prediction horizon (1, 2, or 5)
]


def compute_supertrend_reversal_matrix(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0,
    band_buffer_pct: float = 0.0
) -> pd.DataFrame:
    """
    Computes standard Supertrend alongside rich technical indicator features
    for historical walk-forward modeling or vectorized inference.
    Strictly uses information available up to each completed candle timestamp (no lookahead).
    """
    clean_df = sanitize_candles(df)
    if clean_df.empty or len(clean_df) < 5:
        return clean_df

    st_df = compute_supertrend(clean_df, period=period, multiplier=multiplier, band_buffer_pct=band_buffer_pct)
    c = st_df['close']
    h = st_df['high']
    l = st_df['low']
    o = st_df['open']
    vol = st_df['volume'] if 'volume' in st_df.columns else pd.Series(0.0, index=st_df.index)
    atr = st_df['atr'].replace(0.0, 0.5)

    # 1. Returns
    st_df['return_1m'] = c.pct_change(1).fillna(0.0) * 100.0
    st_df['return_2m'] = c.pct_change(2).fillna(0.0) * 100.0
    st_df['return_3m'] = c.pct_change(3).fillna(0.0) * 100.0
    st_df['return_5m'] = c.pct_change(5).fillna(0.0) * 100.0

    # 2. Momentum
    st_df['momentum_3m'] = (c - c.shift(3)).fillna(0.0)
    st_df['momentum_5m'] = (c - c.shift(5)).fillna(0.0)

    # 3. Supertrend Band Distance, Band Slope, and Distance Change
    st_df['distance_to_band_atr'] = (np.abs(c - st_df['supertrend']) / atr).fillna(0.0)
    st_df['band_slope_3m'] = ((st_df['supertrend'] - st_df['supertrend'].shift(3)) / atr).fillna(0.0)
    st_df['dist_change_3m'] = (st_df['distance_to_band_atr'] - st_df['distance_to_band_atr'].shift(3)).fillna(0.0)

    # 4. RSI & Changes
    rsi = compute_rsi(c, period=14)
    st_df['rsi_14'] = rsi
    st_df['delta_rsi_3m'] = (rsi - rsi.shift(3)).fillna(0.0)
    st_df['delta_rsi_5m'] = (rsi - rsi.shift(5)).fillna(0.0)

    # 5. MACD & Histogram Change
    _, _, macd_hist = compute_macd(c, fast=12, slow=26, signal=9)
    st_df['macd_hist'] = macd_hist
    st_df['delta_macd_hist_3m'] = (macd_hist - macd_hist.shift(3)).fillna(0.0)

    # 6. EMA Slopes normalized by ATR
    ema_9 = c.ewm(span=9, adjust=False).mean()
    ema_21 = c.ewm(span=21, adjust=False).mean()
    st_df['ema_9_slope_3m'] = ((ema_9 - ema_9.shift(3)) / atr).fillna(0.0)
    st_df['ema_21_slope_3m'] = ((ema_21 - ema_21.shift(3)) / atr).fillna(0.0)

    # 7. Volatility & ATR Ratio
    st_df['atr_ratio_5m'] = (atr / atr.shift(5).replace(0.0, np.nan)).fillna(1.0)
    st_df['volatility_10m'] = st_df['return_1m'].rolling(window=10, min_periods=2).std().fillna(0.0)

    # 8. Candle Range relative to ATR
    st_df['candle_range_atr'] = ((h - l) / atr).fillna(1.0)
    st_df['candle_body_atr'] = ((c - o) / atr).fillna(0.0)

    # 9. Volume Features
    vol_num = pd.to_numeric(vol, errors='coerce').fillna(0.0)
    vol_sma20 = vol_num.rolling(window=20, min_periods=1).mean().replace(0.0, 1.0)
    st_df['vol_ratio_20m'] = (vol_num / vol_sma20).fillna(1.0)
    st_df['vol_change_1m'] = vol_num.pct_change(1).replace([np.inf, -np.inf], 0.0).fillna(0.0) * 100.0

    # 10. Multi-Timeframe 5-Minute Context (derived causally from completed 1m windows)
    h5 = h.rolling(5, min_periods=1).max()
    l5 = l.rolling(5, min_periods=1).min()
    st_df['mtf_5m_return'] = c.pct_change(5).fillna(0.0) * 100.0
    st_df['mtf_5m_rsi'] = compute_rsi(c, period=min(len(c), 50)).fillna(50.0)
    st5_df = compute_supertrend(pd.DataFrame({'open': o, 'high': h5, 'low': l5, 'close': c, 'volume': vol}), period=period, multiplier=multiplier)
    st_df['mtf_5m_trend'] = st5_df['supertrend_direction'].fillna(1.0)

    # 11. Trend Duration (bars in current trend) & Adverse Momentum
    dirs = st_df['supertrend_direction'].values
    n = len(dirs)
    trend_dur = np.zeros(n, dtype=np.float32)
    mom_adv_3m = np.zeros(n, dtype=np.float32)
    mom_adv_5m = np.zeros(n, dtype=np.float32)

    dur = 1
    for i in range(n):
        if i > 0:
            if dirs[i] == dirs[i - 1]:
                dur += 1
            else:
                dur = 1
        trend_dur[i] = dur

        # Direction-aligned adverse momentum:
        # If BUY trend (dir == 1), adverse momentum is price drop (c[t-k] - c[t]) / ATR
        # If SELL trend (dir == -1), adverse momentum is price rise (c[t] - c[t-k]) / ATR
        d_sign = 1.0 if dirs[i] == 1 else -1.0
        atr_val = max(0.2, float(atr.iloc[i]))
        if i >= 3:
            mom_adv_3m[i] = -d_sign * float(c.iloc[i] - c.iloc[i - 3]) / atr_val
        if i >= 5:
            mom_adv_5m[i] = -d_sign * float(c.iloc[i] - c.iloc[i - 5]) / atr_val

    st_df['trend_duration_bars'] = trend_dur
    st_df['mom_adverse_3m'] = mom_adv_3m
    st_df['mom_adverse_5m'] = mom_adv_5m
    st_df['direction_code'] = st_df['supertrend_direction']

    return st_df.replace([np.inf, -np.inf], 0.0).fillna(0.0)


def extract_supertrend_reversal_feature_vector(
    candles_or_df: Any,
    horizon_minutes: int = 5,
    current_price: Optional[float] = None,
    period: int = 10,
    multiplier: float = 3.0,
    exclude_incomplete_candle: bool = True
) -> Tuple[np.ndarray, Dict[str, Any], bool]:
    """
    Extracts the calibrated reversal feature vector strictly from completed candles.
    When exclude_incomplete_candle is True:
      - If candles contain a developing/unclosed bar, features are extracted from the
        last closed completed candle to prevent lookahead or mid-bar noise leakage.
    Returns:
        (feature_vector: np.ndarray, feature_dict: Dict, has_sufficient_history: bool)
    """
    ckey = get_candle_sequence_key(candles_or_df)
    cache_key = (
        ckey,
        int(horizon_minutes),
        round(current_price, 2) if current_price is not None else None,
        int(period),
        float(multiplier),
        bool(exclude_incomplete_candle)
    ) if ckey is not None else None

    if cache_key is not None and cache_key in _REVERSAL_FEAT_CACHE:
        cached_vec, cached_d, cached_suff = _REVERSAL_FEAT_CACHE[cache_key]
        return cached_vec.copy(), cached_d.copy(), cached_suff

    clean_df = sanitize_candles(candles_or_df)
    has_sufficient = bool(len(clean_df) >= 15)

    if clean_df.empty:
        # Return fallback zeros vector
        vec = np.zeros(len(SUPERTREND_REVERSAL_FEATURE_NAMES), dtype=np.float32)
        vec[-1] = float(horizon_minutes)
        d = {k: 0.0 for k in SUPERTREND_REVERSAL_FEATURE_NAMES}
        d['horizon_minutes'] = float(horizon_minutes)
        return vec, d, False

    if len(clean_df) > 100:
        clean_df = clean_df.iloc[-100:].reset_index(drop=True)

    df = clean_df.copy()
    matrix = compute_supertrend_reversal_matrix(df, period=period, multiplier=multiplier)

    # In live polling, if the last candle in df is an open developing candle (or current_price is passed),
    # completed candle is at iloc[-2] if len >= 2; else iloc[-1].
    if exclude_incomplete_candle and current_price is not None and len(matrix) >= 2:
        last_row = matrix.iloc[-2]
    else:
        last_row = matrix.iloc[-1]

    feature_dict = {}
    vec = []
    for f in SUPERTREND_REVERSAL_FEATURE_NAMES:
        if f == 'horizon_minutes':
            val = float(horizon_minutes)
        elif f in last_row:
            val = float(last_row[f])
        else:
            val = 0.0
        feature_dict[f] = val
        vec.append(val)

    res_vec = np.array(vec, dtype=np.float32)
    if cache_key is not None:
        if len(_REVERSAL_FEAT_CACHE) > 256:
            _REVERSAL_FEAT_CACHE.pop(next(iter(_REVERSAL_FEAT_CACHE)))
        _REVERSAL_FEAT_CACHE[cache_key] = (res_vec.copy(), feature_dict.copy(), has_sufficient)

    return res_vec, feature_dict, has_sufficient

