import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from app.services.market_data import normalize_ticker
from app.services.prediction import predict_target_probability
from app.services.adaptive_threshold import adaptive_engine


def run_historical_backtest(
    symbol: str,
    target_percent: float = 3.0,
    warning_minutes: int = 10,
    direction: str = "UP",
    days: int = 5
) -> Dict[str, Any]:
    """
    Historical Backtesting Engine (Section 25 of StockPulse Specification):
    Strictly enforces zero lookahead leakage:
    - Features at bar i only utilize market data from bars 0..i.
    - Ground-truth target event is evaluated over the future prediction window [i+1..i+W].
    - Computes Precision, Recall, F1 Score, False Alerts, Missed Events,
      and Average Warning Lead Time (time between early warning and target reaching).
    """
    clean_sym = symbol.strip().upper()
    norm_sym = normalize_ticker(clean_sym)
    is_up = direction.upper() == "UP"

    # Fetch 1-minute historical data (up to 5-7 days available in yfinance)
    try:
        ticker = yf.Ticker(norm_sym)
        df = ticker.history(period=f"{min(days, 7)}d", interval="1m", auto_adjust=False)
        if df.empty or len(df) < 50:
            # Fallback to 5-minute interval if 1-minute is sparse
            df = ticker.history(period="1mo", interval="5m", auto_adjust=False)
            interval_min = 5
        else:
            interval_min = 1
    except Exception as e:
        return {
            "status": "ERROR",
            "error": f"Failed fetching market data for {symbol}: {str(e)}",
            "symbol": clean_sym
        }

    if df.empty or len(df) < 30:
        return {
            "status": "ERROR",
            "error": f"Insufficient historical bars for {clean_sym} ({len(df)} bars found)",
            "symbol": clean_sym
        }

    df.columns = [c.lower() for c in df.columns]
    close_prices = df["close"].values
    high_prices = df["high"].values
    low_prices = df["low"].values
    vol_values = df["volume"].values

    n_bars = len(close_prices)
    # Window in terms of bars
    window_bars = max(1, int(round(warning_minutes / interval_min)))

    # Fetch automatic adaptive threshold for this configuration
    thresh_eval = adaptive_engine.get_adaptive_threshold(
        symbol=clean_sym,
        target_percent=target_percent,
        warning_minutes=warning_minutes,
        direction=direction
    )
    auto_threshold = thresh_eval.get("threshold", 0.78)

    tp = 0
    fp = 0
    fn = 0
    tn = 0
    lead_times: List[float] = []
    events_log: List[Dict[str, Any]] = []

    # Minimum warm-up period for rolling indicators (RSI 14, 20-bar volume SMA)
    warmup = 25
    step = max(1, window_bars // 2)  # evaluate every few bars to avoid near-duplicate contiguous overlaps

    for i in range(warmup, n_bars - window_bars, step):
        # 1. Past data only up to bar i (ZERO LOOKAHEAD)
        past_closes = close_prices[:i + 1]
        past_vols = vol_values[:i + 1]

        curr_price = float(past_closes[-1])
        ref_price = curr_price

        # Quick indicator calculation using past slices
        ret_1m = ((past_closes[-1] - past_closes[-2]) / past_closes[-2]) * 100.0 if len(past_closes) > 1 else 0.0
        ret_5m = ((past_closes[-1] - past_closes[-6]) / past_closes[-6]) * 100.0 if len(past_closes) > 5 else 0.0
        roc_10m = ((past_closes[-1] - past_closes[-11]) / past_closes[-11]) * 100.0 if len(past_closes) > 10 else 0.0

        pct_changes = np.diff(past_closes[-11:]) / past_closes[-11:-1] * 100.0 if len(past_closes) > 11 else np.array([0.1])
        vol_10m = float(np.std(pct_changes)) if len(pct_changes) > 1 else 0.15

        vol_slice = past_vols[-20:]
        vol_ratio = float(past_vols[-1] / (np.mean(vol_slice) + 1e-6)) if len(vol_slice) > 0 else 1.0

        # RSI calculation on past slice
        deltas = np.diff(past_closes[-15:])
        gains = np.maximum(deltas, 0)
        losses = np.maximum(-deltas, 0)
        avg_gain = float(np.mean(gains)) if len(gains) > 0 else 0.0
        avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0
        rs = avg_gain / (avg_loss + 1e-6)
        rsi = float(100 - (100 / (1 + rs)))

        sim_snapshot = {
            "price": curr_price,
            "change_pct": ret_5m,
            "features": {
                "return_1m": ret_1m,
                "return_5m": ret_5m,
                "roc_10m": roc_10m,
                "volatility_10m": max(vol_10m, 0.05),
                "vol_ratio_20m": min(max(vol_ratio, 0.5), 4.0),
                "rsi_14": rsi,
                "macd_hist": 0.0
            }
        }

        # 2. Predict probability at time i
        pred = predict_target_probability(
            symbol=clean_sym,
            target_percent=target_percent,
            warning_minutes=warning_minutes,
            direction=direction,
            market_snapshot=sim_snapshot
        )
        prob = pred["probability"]
        is_early_warning = prob >= auto_threshold

        # 3. Ground Truth: Check future window [i+1 .. i+window_bars]
        if is_up:
            target_price = ref_price * (1.0 + (target_percent / 100.0))
            future_highs = high_prices[i + 1: i + 1 + window_bars]
            hits = np.where(future_highs >= target_price)[0]
            target_reached = len(hits) > 0
            first_hit_bar = hits[0] if target_reached else None
        else:
            target_price = ref_price * (1.0 - (target_percent / 100.0))
            future_lows = low_prices[i + 1: i + 1 + window_bars]
            hits = np.where(future_lows <= target_price)[0]
            target_reached = len(hits) > 0
            first_hit_bar = hits[0] if target_reached else None

        # 4. Metrics & Lead Time Computation
        if target_reached and is_early_warning:
            tp += 1
            # Lead time: time in minutes between prediction bar and first target hit
            lead_time_m = (first_hit_bar + 1) * interval_min
            lead_times.append(lead_time_m)
            if len(events_log) < 15:
                events_log.append({
                    "timestamp": str(df.index[i]),
                    "event": "TRUE_POSITIVE",
                    "price": curr_price,
                    "target_price": round(target_price, 2),
                    "probability": round(prob, 3),
                    "lead_time_minutes": lead_time_m
                })
        elif (not target_reached) and is_early_warning:
            fp += 1
            if len(events_log) < 15:
                events_log.append({
                    "timestamp": str(df.index[i]),
                    "event": "FALSE_ALERT",
                    "price": curr_price,
                    "target_price": round(target_price, 2),
                    "probability": round(prob, 3),
                    "lead_time_minutes": None
                })
        elif target_reached and (not is_early_warning):
            fn += 1
        else:
            tn += 1

    total_evals = tp + fp + fn + tn
    precision = float(round(tp / (tp + fp), 4)) if (tp + fp) > 0 else 0.0
    recall = float(round(tp / (tp + fn), 4)) if (tp + fn) > 0 else 0.0
    f1 = float(round(2 * precision * recall / (precision + recall), 4)) if (precision + recall) > 0 else 0.0
    avg_lead_time = float(round(np.mean(lead_times), 2)) if lead_times else 0.0

    return {
        "status": "SUCCESS",
        "symbol": clean_sym,
        "target_percent": target_percent,
        "prediction_window_minutes": warning_minutes,
        "direction": direction,
        "automatic_threshold": auto_threshold,
        "historical_days_evaluated": days,
        "total_evaluations": total_evals,
        "target_events_count": tp + fn,
        "early_warnings_triggered": tp + fp,
        "true_positives": tp,
        "false_alerts": fp,
        "missed_events": fn,
        "true_negatives": tn,
        "precision": precision,
        "recall": recall,
        "f1_score": f1,
        "average_warning_lead_time_minutes": avg_lead_time,
        "lead_time_samples_count": len(lead_times),
        "recent_event_samples": events_log
    }
