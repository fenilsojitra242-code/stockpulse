import numpy as np
import pandas as pd
from typing import Optional, Dict, Any


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


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Average True Range (ATR) with strict OHLC data sanitization."""
    # Ensure close has valid positive numbers
    clean_close = pd.to_numeric(close, errors='coerce').replace(0.0, np.nan).ffill().bfill().fillna(100.0)
    clean_high = pd.to_numeric(high, errors='coerce').replace(0.0, np.nan).fillna(clean_close)
    clean_low = pd.to_numeric(low, errors='coerce').replace(0.0, np.nan).fillna(clean_close)

    # Physical validity: low > 0, low <= close, high >= close, high >= low
    clean_low = np.maximum(0.01, np.minimum(clean_low, clean_close))
    clean_high = np.maximum(clean_high, clean_close)
    clean_low = np.minimum(clean_low, clean_high)

    tr1 = (clean_high - clean_low).abs()
    tr2 = (clean_high - clean_close.shift(1)).abs()
    tr3 = (clean_low - clean_close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Sanity guard: A single 1-minute candle TR should never exceed 4% of stock price
    max_reasonable_tr = (clean_close * 0.04).clip(lower=0.50)
    tr = tr.clip(lower=0.01, upper=max_reasonable_tr)

    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    atr = atr.fillna(tr.mean() if not tr.empty else 1.0)
    atr = atr.clip(lower=0.01, upper=max_reasonable_tr)
    return atr


def compute_supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0
) -> pd.DataFrame:
    """
    Computes standard SuperTrend indicator with ATR volatility bands.
    Guarantees strictly positive valid bands and reliable trend lines.
    Returns copy of df with:
      - 'supertrend': the active SuperTrend line price
      - 'supertrend_direction': 1 for BULLISH (BUY), -1 for BEARISH (SELL)
      - 'supertrend_signal': 'BUY' on bull flip, 'SELL' on bear flip, None otherwise
      - 'supertrend_upper': upper resistance band
      - 'supertrend_lower': lower support band
    """
    if df.empty:
        return df

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
        return data

    close = pd.to_numeric(data['close'], errors='coerce').replace(0.0, np.nan).ffill().bfill().fillna(100.0)
    open_p = pd.to_numeric(data.get('open', close), errors='coerce').replace(0.0, np.nan).fillna(close)
    high = pd.to_numeric(data['high'], errors='coerce').replace(0.0, np.nan).fillna(close)
    low = pd.to_numeric(data['low'], errors='coerce').replace(0.0, np.nan).fillna(close)

    # Physical validity constraints: low must be > 0 and low <= min(open, close)
    low = np.maximum(0.01, np.minimum(low, np.minimum(open_p, close)))
    high = np.maximum(high, np.maximum(open_p, close))
    low = np.minimum(low, high)

    atr = compute_atr(high, low, close, period=period)
    hl2 = (high + low) / 2.0
    basic_upper = hl2 + (float(multiplier) * atr)
    basic_lower = np.maximum(0.01, hl2 - (float(multiplier) * atr))

    final_upper = [0.0] * n
    final_lower = [0.0] * n
    trend = [1] * n
    supertrend = [0.0] * n
    signal = [None] * n

    # Initialize first candle with positive bounds
    final_upper[0] = max(0.01, float(basic_upper.iloc[0]))
    final_lower[0] = max(0.01, float(basic_lower.iloc[0]))
    trend[0] = 1 if float(close.iloc[0]) >= final_lower[0] else -1
    supertrend[0] = final_lower[0] if trend[0] == 1 else final_upper[0]

    for i in range(1, n):
        b_up = max(0.01, float(basic_upper.iloc[i]))
        b_low = max(0.01, float(basic_lower.iloc[i]))
        c_prev = float(close.iloc[i - 1])
        c_curr = float(close.iloc[i])

        # Final Lower Band (Support)
        if b_low > final_lower[i - 1] or c_prev < final_lower[i - 1]:
            final_lower[i] = b_low
        else:
            final_lower[i] = final_lower[i - 1]

        # Final Upper Band (Resistance)
        if b_up < final_upper[i - 1] or c_prev > final_upper[i - 1]:
            final_upper[i] = b_up
        else:
            final_upper[i] = final_upper[i - 1]

        # Invariant: Support line must strictly remain positive and <= Upper Band
        final_lower[i] = max(0.01, final_lower[i])
        final_upper[i] = max(final_lower[i], final_upper[i])

        # Trend direction determination
        prev_trend = trend[i - 1]
        if prev_trend == 1:
            if c_curr < final_lower[i]:
                trend[i] = -1
                signal[i] = "SELL"
            else:
                trend[i] = 1
        else:
            if c_curr > final_upper[i]:
                trend[i] = 1
                signal[i] = "BUY"
            else:
                trend[i] = -1

        supertrend[i] = final_lower[i] if trend[i] == 1 else final_upper[i]

    data['supertrend'] = [round(float(x), 2) for x in supertrend]
    data['supertrend_direction'] = trend
    data['supertrend_signal'] = signal
    data['supertrend_upper'] = [round(float(x), 2) for x in final_upper]
    data['supertrend_lower'] = [round(float(x), 2) for x in final_lower]
    return data


def analyze_supertrend_hit(
    candles_or_df: Any,
    current_price: Optional[float] = None,
    period: int = 10,
    multiplier: float = 3.0,
    proximity_points: float = 1.0,
    previous_direction: Optional[str] = None
) -> Dict[str, Any]:
    """
    Evaluates latest candles against SuperTrend levels for:
    1. Physical proximity (within user's target points).
    2. Candle stability & decisiveness (prevents premature micro-tick predictions).
    3. Confirmed SuperTrend flip / hit signals.

    Trend Anchoring Rule:
    - The confirmed trend of the market is anchored to the last completed/closed candle.
    - A forming candle does NOT flip the trend on momentary micro-ticks; it must either
      close across the line or demonstrate mature, decisive penetration.
    """
    if isinstance(candles_or_df, pd.DataFrame):
        df = candles_or_df.copy()
    elif isinstance(candles_or_df, list):
        df = pd.DataFrame([dict(c) for c in candles_or_df])
    else:
        df = pd.DataFrame()

    prox_limit = max(0.01, float(proximity_points))

    if df.empty or len(df) < 2:
        p = float(current_price or 100.0)
        return {
            "current_price": p,
            "supertrend_price": p,
            "direction": "BUY",
            "previous_direction": previous_direction or "BUY",
            "target_direction": "SELL",
            "distance_points": 0.0,
            "proximity_threshold": prox_limit,
            "is_approaching": False,
            "is_prediction_ready": False,
            "is_flipped": False,
            "flip_signal": None,
            "candle_open": p,
            "candle_close": p,
            "candle_points": 0.0,
            "candle_color": "GREEN",
            "candle_body_size": 0.0,
            "is_candle_stable": False,
            "candle_matches_target": False,
            "candle_age_seconds": 0.0,
            "prev_candle_points": 0.0,
            "prev_candle_color": "GREEN",
            "series": []
        }

    # Normalize columns to lowercase
    df.columns = [c.lower() for c in df.columns]
    if 'close' not in df.columns and 'price' in df.columns:
        df['close'] = df['price']

    # Update latest candle close/high/low with current_price so SuperTrend calculates on real-time live price
    if current_price is not None and not df.empty:
        cp_val = float(current_price)
        last_idx = df.index[-1]
        df.loc[last_idx, 'close'] = cp_val
        if 'high' in df.columns:
            df.loc[last_idx, 'high'] = max(float(df.loc[last_idx, 'high']), cp_val)
        if 'low' in df.columns:
            cur_low = float(df.loc[last_idx, 'low'])
            df.loc[last_idx, 'low'] = min(cur_low, cp_val) if cur_low > 0 else cp_val

    st_df = compute_supertrend(df, period=period, multiplier=multiplier)
    last_row = st_df.iloc[-1]

    cp = float(current_price if current_price is not None else last_row['close'])

    # Determine confirmed baseline trend from the last closed/completed candle
    if len(st_df) >= 2:
        prev_row = st_df.iloc[-2]
        closed_dir_code = int(prev_row['supertrend_direction'])
        closed_trend_dir = "BUY" if closed_dir_code == 1 else "SELL"
        prev_open = float(prev_row.get('open', prev_row.get('close', cp)))
        prev_close = float(prev_row.get('close', cp))
        prev_candle_points = round(prev_close - prev_open, 2)
        prev_candle_color = "GREEN" if prev_candle_points >= 0 else "RED"

        # Did the last closed candle complete a flip?
        if len(st_df) >= 3:
            prev_prev_dir_code = int(st_df['supertrend_direction'].iloc[-3])
            did_closed_candle_flip = (closed_dir_code != prev_prev_dir_code)
        else:
            did_closed_candle_flip = False
    else:
        closed_dir_code = int(last_row['supertrend_direction'])
        closed_trend_dir = "BUY" if closed_dir_code == 1 else "SELL"
        prev_candle_points = 0.0
        prev_candle_color = "GREEN"
        did_closed_candle_flip = False

    # Forming/latest candle metrics
    c_open = float(last_row.get('open', cp))
    c_close = cp
    candle_points = round(c_close - c_open, 2)
    candle_color = "GREEN" if candle_points >= 0 else "RED"
    candle_body_abs = round(abs(candle_points), 2)

    # Estimate candle age in seconds from timestamp if present
    candle_age_seconds = 60.0
    full_time_val = last_row.get('full_time') or last_row.get('time')
    if full_time_val:
        try:
            c_dt = pd.to_datetime(full_time_val)
            if c_dt.tzinfo is not None:
                candle_age_seconds = max(0.0, (pd.Timestamp.now(c_dt.tz) - c_dt).total_seconds())
        except Exception:
            pass

    # Upper and Lower SuperTrend lines for the current candle
    st_upper = float(last_row['supertrend_upper'])
    st_lower = float(last_row['supertrend_lower'])

    # Forming candle breakout / breakdown evaluation:
    # A forming candle can ONLY confirm a flip before closing if it has decisively
    # penetrated beyond the line with direction conviction and mature candle age.
    min_decisive_penetration = max(1.20, min(3.0, prox_limit * 0.50))
    forming_flipped = False
    forming_flip_dir = None

    if closed_trend_dir == "SELL":
        # Price is below resistance (st_upper). A BUY flip requires breaking above st_upper.
        if (cp - st_upper) >= min_decisive_penetration and candle_color == "GREEN" and candle_points > 0 and candle_body_abs >= 0.35 and candle_age_seconds >= 30.0:
            forming_flipped = True
            forming_flip_dir = "BUY"
    elif closed_trend_dir == "BUY":
        # Price is above support (st_lower). A SELL flip requires breaking below st_lower.
        if (st_lower - cp) >= min_decisive_penetration and candle_color == "RED" and candle_points < 0 and candle_body_abs >= 0.35 and candle_age_seconds >= 30.0:
            forming_flipped = True
            forming_flip_dir = "SELL"

    # Current established trend:
    if did_closed_candle_flip:
        curr_dir = closed_trend_dir
        is_flipped = True
    elif forming_flipped:
        curr_dir = forming_flip_dir
        is_flipped = True
    else:
        curr_dir = closed_trend_dir
        is_flipped = False

    # Check previous_direction constraint: flip is only new if different from previous_direction
    resolved_prev_dir = previous_direction or closed_trend_dir
    if resolved_prev_dir and curr_dir == resolved_prev_dir:
        is_flipped = False

    # Active SuperTrend line and target direction
    if curr_dir == "SELL":
        st_p = st_upper
        target_dir = "BUY"
        distance_points = round(max(0.0, st_upper - cp), 2)
    else:
        st_p = st_lower
        target_dir = "SELL"
        distance_points = round(max(0.0, cp - st_lower), 2)

    # Candle Stability Rule:
    # A newly created candle (with 0 or tiny body) is not yet stable.
    # It must develop body conviction or mature beyond 15s with decisive movement.
    stability_threshold = max(0.20, min(0.60, prox_limit * 0.20))
    is_candle_stable = bool(
        (candle_body_abs >= stability_threshold) or
        (candle_age_seconds >= 15.0 and candle_body_abs >= 0.15)
    )

    # Directional Alignment Rule:
    # BUY target requires a decided GREEN candle (candle_points > 0).
    # SELL target requires a decided RED candle (candle_points < 0).
    candle_matches_target = (
        (target_dir == "BUY" and candle_color == "GREEN" and candle_points > 0) or
        (target_dir == "SELL" and candle_color == "RED" and candle_points < 0)
    )

    flip_signal = curr_dir if is_flipped else None

    # Physical proximity (price is physically within user's points threshold of the active line)
    is_approaching = bool(distance_points <= prox_limit and not is_flipped and st_p > 0.01)

    # Model prediction readiness:
    # Ready ONLY when physically approaching AND candle is decided & stable
    is_prediction_ready = bool(
        is_approaching and
        candle_matches_target and
        is_candle_stable and
        not is_flipped and
        st_p > 0.01
    )

    # Series for chart visualization
    series_points = []
    for idx, row in st_df.iterrows():
        t_val = row.get('time') or row.get('full_time') or str(idx)
        raw_price = row.get('close', row.get('price', 0.0))
        p_val = round(float(raw_price), 2) if pd.notna(raw_price) else round(cp, 2)
        raw_st = row.get('supertrend')
        st_val = round(float(raw_st), 2) if pd.notna(raw_st) else round(cp, 2)
        raw_sig = row.get('supertrend_signal')
        sig_clean = str(raw_sig) if (pd.notna(raw_sig) and str(raw_sig) in ("BUY", "SELL")) else None

        series_points.append({
            "time": str(t_val),
            "price": p_val,
            "supertrend": max(0.01, st_val),
            "direction": "BUY" if row['supertrend_direction'] == 1 else "SELL",
            "signal": sig_clean
        })

    if series_points and not is_flipped:
        series_points[-1]["direction"] = curr_dir
        if series_points[-1]["signal"] is not None:
            series_points[-1]["signal"] = None

    return {
        "current_price": round(cp, 2),
        "supertrend_price": round(st_p, 2),
        "direction": curr_dir,
        "previous_direction": resolved_prev_dir,
        "target_direction": target_dir,
        "distance_points": distance_points,
        "proximity_threshold": prox_limit,
        "is_approaching": is_approaching,
        "is_prediction_ready": is_prediction_ready,
        "is_flipped": is_flipped,
        "flip_signal": flip_signal,
        "candle_open": round(c_open, 2),
        "candle_close": round(c_close, 2),
        "candle_points": candle_points,
        "candle_color": candle_color,
        "candle_body_size": candle_body_abs,
        "is_candle_stable": is_candle_stable,
        "candle_matches_target": candle_matches_target,
        "candle_age_seconds": round(candle_age_seconds, 1),
        "prev_candle_points": prev_candle_points,
        "prev_candle_color": prev_candle_color,
        "series": series_points
    }



def compute_market_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes rich intraday technical features from OHLCV 1-minute candle data.
    Input df must contain: ['open', 'high', 'low', 'close', 'volume'].
    Returns df with all computed features.
    """
    if df.empty or len(df) < 5:
        return df

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
