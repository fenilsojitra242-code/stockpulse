import numpy as np
import pandas as pd
from typing import Dict, Any, Optional
from app.services.feature_engine import sanitize_candles, compute_atr, compute_rsi, compute_macd


def classify_market_regime(candles_or_df: Any) -> Dict[str, Any]:
    """
    Classifies the current market regime based strictly on causal past candle data:
    1. Trend Regime: BULLISH_TREND, BEARISH_TREND, or SIDEWAYS_CHOPPY.
    2. Volatility Regime: HIGH_VOLATILITY, LOW_VOLATILITY, or NORMAL_VOLATILITY.
    3. Quantitative regime guidance for early-warning thresholds and confirming candles.
    """
    df = sanitize_candles(candles_or_df)
    if df.empty or len(df) < 5:
        return {
            "primary_regime": "SIDEWAYS_CHOPPY",
            "volatility_regime": "NORMAL_VOLATILITY",
            "trend_strength": 0.50,
            "atr_current": 1.0,
            "atr_median_30": 1.0,
            "volatility_ratio": 1.0,
            "recommended_min_confirming_candles": 2,
            "recommended_min_penetration_atr": 0.15,
            "recommended_threshold_boost": 0.05,
            "is_sufficient_history": False
        }

    c = df['close']
    h = df['high']
    l = df['low']
    n = len(c)

    # 1. Moving Averages & Trend Alignment
    ema_9 = c.ewm(span=min(9, n), adjust=False).mean()
    ema_21 = c.ewm(span=min(21, n), adjust=False).mean()
    sma_20 = c.rolling(window=min(20, n), min_periods=3).mean()

    curr_c = float(c.iloc[-1])
    curr_ema9 = float(ema_9.iloc[-1])
    curr_ema21 = float(ema_21.iloc[-1])
    curr_sma20 = float(sma_20.iloc[-1])

    # 2. RSI & MACD
    rsi = compute_rsi(c, period=min(14, max(3, n // 2)))
    curr_rsi = float(rsi.iloc[-1])
    _, _, macd_hist = compute_macd(c, fast=12, slow=26, signal=9)
    curr_hist = float(macd_hist.iloc[-1])

    # 3. Volatility Regime via ATR ratio
    atr_series = compute_atr(h, l, c, period=min(14, max(3, n // 2)))
    curr_atr = max(0.01, float(atr_series.iloc[-1]))
    atr_median_30 = max(0.01, float(atr_series.tail(min(30, n)).median()))
    vol_ratio = curr_atr / atr_median_30

    if vol_ratio >= 1.25:
        vol_regime = "HIGH_VOLATILITY"
    elif vol_ratio <= 0.75:
        vol_regime = "LOW_VOLATILITY"
    else:
        vol_regime = "NORMAL_VOLATILITY"

    # 4. Trend Direction and Separation
    ema_sep_pct = abs(curr_ema9 - curr_ema21) / curr_c * 100.0
    is_bullish_aligned = (curr_c >= curr_ema9 >= curr_ema21) and (curr_rsi >= 50.0)
    is_bearish_aligned = (curr_c <= curr_ema9 <= curr_ema21) and (curr_rsi <= 50.0)

    # Directional momentum score
    # Count consecutive higher highs / higher lows or lower highs / lower lows
    if n >= 5:
        highs_tail = h.tail(5).values
        lows_tail = l.tail(5).values
        hh_count = sum(1 for i in range(1, 5) if highs_tail[i] > highs_tail[i-1])
        ll_count = sum(1 for i in range(1, 5) if lows_tail[i] < lows_tail[i-1])
    else:
        hh_count = 2
        ll_count = 2

    # Chop test: small separation and fluctuating histogram
    is_chop = (ema_sep_pct < 0.15 and 45.0 <= curr_rsi <= 55.0) or (hh_count < 3 and ll_count < 3 and ema_sep_pct < 0.25)

    if is_chop:
        primary_regime = "SIDEWAYS_CHOPPY"
        trend_strength = round(max(0.1, min(0.4, ema_sep_pct * 2.0)), 2)
        min_confirming = 2
        min_penetration = 0.20
        thresh_boost = 0.08
    elif is_bullish_aligned and hh_count >= 3:
        primary_regime = "BULLISH_TREND"
        trend_strength = round(min(0.95, 0.5 + ema_sep_pct * 1.5), 2)
        min_confirming = 1
        min_penetration = 0.10
        thresh_boost = 0.0
    elif is_bearish_aligned and ll_count >= 3:
        primary_regime = "BEARISH_TREND"
        trend_strength = round(min(0.95, 0.5 + ema_sep_pct * 1.5), 2)
        min_confirming = 1
        min_penetration = 0.10
        thresh_boost = 0.0
    else:
        # Mild bias based on EMA 9 vs 21
        if curr_ema9 > curr_ema21:
            primary_regime = "BULLISH_TREND"
        else:
            primary_regime = "BEARISH_TREND"
        trend_strength = 0.55
        min_confirming = 1
        min_penetration = 0.12
        thresh_boost = 0.02

    # In high volatility, noise increases: require higher penetration
    if vol_regime == "HIGH_VOLATILITY":
        min_penetration = max(min_penetration, 0.20)
        thresh_boost = max(thresh_boost, 0.05)

    return {
        "primary_regime": primary_regime,
        "volatility_regime": vol_regime,
        "trend_strength": trend_strength,
        "atr_current": round(curr_atr, 2),
        "atr_median_30": round(atr_median_30, 2),
        "volatility_ratio": round(vol_ratio, 2),
        "recommended_min_confirming_candles": min_confirming,
        "recommended_min_penetration_atr": min_penetration,
        "recommended_threshold_boost": thresh_boost,
        "is_sufficient_history": bool(n >= 15)
    }
