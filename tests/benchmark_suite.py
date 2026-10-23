import os
import sys
import time
import tempfile
import atexit

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Set up dedicated isolated temporary SQLite database BEFORE any app or database imports
_BENCHMARK_TMP_DIR = tempfile.TemporaryDirectory()
_BENCHMARK_DB_PATH = os.path.join(_BENCHMARK_TMP_DIR.name, "benchmark_stockpulse.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_BENCHMARK_DB_PATH}"

def _cleanup_bench_db():
    try:
        _BENCHMARK_TMP_DIR.cleanup()
    except Exception:
        pass

atexit.register(_cleanup_bench_db)

import threading
import tracemalloc
from datetime import datetime

import pandas as pd
import numpy as np
import requests
import uvicorn

# Begin memory tracking
tracemalloc.start()
t0_total = time.perf_counter()

print("=" * 70)
print("StockPulse Performance Profiler & Benchmark")
print("=" * 70)

# 1. Startup Time Measurement (Imports)
t0_import = time.perf_counter()
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import app.database.database as db_mod

# Explicitly create and rebind isolated test engine to handle cases where module was pre-imported
_isolated_engine = create_engine(
    f"sqlite:///{_BENCHMARK_DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 15}
)
_IsolatedSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_isolated_engine)
db_mod.engine = _isolated_engine
db_mod.SessionLocal = _IsolatedSessionLocal

from app.main import app
import app.main as app_main_mod
app_main_mod.engine = _isolated_engine
app_main_mod.SessionLocal = _IsolatedSessionLocal

from app.database.database import SessionLocal, engine
# Verify production database is strictly isolated and never touched
import urllib.parse
unquoted_url = urllib.parse.unquote(str(engine.url))
assert "data/stockpulse.db" not in unquoted_url, f"CRITICAL: Production DB targeted by benchmark engine: {engine.url}"
assert _BENCHMARK_DB_PATH in unquoted_url or "benchmark_stockpulse.db" in unquoted_url or ":memory:" in unquoted_url, f"Benchmark engine not pointing to isolated database: {engine.url}"
from app.database.models import User, Alert, Notification, AlertHistory, SystemSetting
from app.services.market_data import (
    get_stock_visualization_data,
    get_latest_market_snapshot,
    _RANGE_CACHE
)
from app.services.feature_engine import analyze_supertrend_hit, compute_market_features
from app.services.prediction import (
    load_model,
    predict_target_probability,
    predict_supertrend_reversal
)
from app.services.alert_engine import (
    get_supertrend_settings,
    evaluate_active_alerts,
    scan_default_watchlist_supertrend
)
t_import = time.perf_counter() - t0_import
print(f"[1] Module Import Duration: {t_import*1000:.2f} ms")

# Start test uvicorn server on a dedicated test port (e.g. 8009)
server_config = uvicorn.Config(app=app, host="127.0.0.1", port=8009, log_level="warning")
server = uvicorn.Server(server_config)
server_thread = threading.Thread(target=server.run, daemon=True)
t0_startup = time.perf_counter()
server_thread.start()

# Wait for server to be responsive
BASE_URL = "http://127.0.0.1:8009"
session = requests.Session()
server_ready = False
for _ in range(50):
    try:
        r = session.get(f"{BASE_URL}/health", timeout=1)
        if r.status_code == 200:
            server_ready = True
            break
    except Exception:
        time.sleep(0.1)

t_server_startup = time.perf_counter() - t0_startup
print(f"[1.1] Server Startup & Ready Duration: {t_server_startup*1000:.2f} ms")

# Measure DB queries baseline
db = SessionLocal()

# 9. Database Query Duration (Cold vs Warm)
from app.services.alert_engine import _SETTINGS_CACHE
_SETTINGS_CACHE.clear()
t0_db_cold = time.perf_counter()
st_settings_cold = get_supertrend_settings(db, user_id=1)
t_db_settings_cold = (time.perf_counter() - t0_db_cold) * 1000.0

t0_db_warm = time.perf_counter()
for _ in range(10):
    st_settings = get_supertrend_settings(db, user_id=1)
t_db_settings_warm = (time.perf_counter() - t0_db_warm) / 10.0 * 1000.0
print(f"[9.1] Database get_supertrend_settings: Cold = {t_db_settings_cold:.2f} ms | Warm = {t_db_settings_warm:.3f} ms")

t0_db_q = time.perf_counter()
for _ in range(10):
    users = db.query(User).all()
    active_alerts = db.query(Alert).filter(Alert.status.in_(["MONITORING", "EARLY_WARNING"])).all()
    notifs = db.query(Notification).order_by(Notification.created_at.desc()).limit(30).all()
    hist = db.query(AlertHistory).order_by(AlertHistory.created_at.desc()).limit(30).all()
t_db_standard = (time.perf_counter() - t0_db_q) / 10.0
print(f"[9.2] Database standard queries (avg of 10): {t_db_standard*1000:.2f} ms")

# Synthetic candles for deterministic testing
n_candles = 360
np.random.seed(42)
prices = 1000.0 + np.cumsum(np.random.randn(n_candles) * 0.5)
candle_list = []
start_dt = datetime(2026, 10, 15, 9, 15)
for i in range(n_candles):
    c_time = (start_dt + pd.Timedelta(minutes=i)).strftime("%I:%M %p")
    p = round(float(prices[i]), 2)
    candle_list.append({
        "time": c_time,
        "full_time": (start_dt + pd.Timedelta(minutes=i)).isoformat(),
        "price": p,
        "open": round(p - 0.2, 2),
        "high": round(p + 0.8, 2),
        "low": round(p - 0.8, 2),
        "close": p,
        "volume": 5000 + i * 10
    })

# Synchronize with background pre-warming so ML unpickling does not starve GIL during benchmark
from app.services.prediction import is_prewarm_complete
for _ in range(40):
    if is_prewarm_complete():
        break
    time.sleep(0.5)

# 6. Supertrend Calculation Duration (Cold vs Warm)
# Reset cache for clean cold-run timing
from app.services.feature_engine import _ANALYZE_ST_CACHE, _COMPUTE_ST_CACHE
_ANALYZE_ST_CACHE.clear()
_COMPUTE_ST_CACHE.clear()

t0_st_cold = time.perf_counter()
res_st_cold = analyze_supertrend_hit(
    candles_or_df=candle_list,
    current_price=candle_list[-1]["price"],
    period=10,
    multiplier=3.0,
    proximity_points=1.0,
    proximity_percent=0.50
)
t_st_cold = (time.perf_counter() - t0_st_cold) * 1000.0

t0_st_warm = time.perf_counter()
iterations_st = 25
for _ in range(iterations_st):
    res_st = analyze_supertrend_hit(
        candles_or_df=candle_list,
        current_price=candle_list[-1]["price"],
        period=10,
        multiplier=3.0,
        proximity_points=1.0,
        proximity_percent=0.50
    )
t_st_warm = (time.perf_counter() - t0_st_warm) / iterations_st * 1000.0
print(f"[6] Supertrend calculation (360 candles): Cold = {t_st_cold:.2f} ms | Warm = {t_st_warm:.3f} ms")

# 7. ML Inference Duration (Cold vs Warm)
from app.services.prediction import _PREDICT_TARGET_PROB_CACHE, _PREDICT_ST_REVERSAL_CACHE
_PREDICT_TARGET_PROB_CACHE.clear()
_PREDICT_ST_REVERSAL_CACHE.clear()

sample_features = {
    "price_change_1m": 0.05,
    "price_change_5m": 0.15,
    "atr_14": 2.5,
    "volatility_ratio": 1.2,
    "rsi_14": 55.0,
    "volume_surge": 1.5,
    "distance_to_supertrend": 0.4
}
sample_snapshot = {
    "symbol": "RELIANCE",
    "price": 2500.0,
    "change_pct": 0.5,
    "latest_market_timestamp": "2026-10-16T15:30:00",
    "features": sample_features
}

# 7.1 predict_target_probability
t0_ml_cold = time.perf_counter()
prob_cold = predict_target_probability(
    symbol="RELIANCE",
    target_percent=0.50,
    warning_minutes=5,
    direction="UP",
    market_snapshot=sample_snapshot
)
t_ml_prob_cold = (time.perf_counter() - t0_ml_cold) * 1000.0

iterations_ml = 25
t0_ml_warm = time.perf_counter()
for _ in range(iterations_ml):
    prob = predict_target_probability(
        symbol="RELIANCE",
        target_percent=0.50,
        warning_minutes=5,
        direction="UP",
        market_snapshot=sample_snapshot
    )
t_ml_prob_warm = (time.perf_counter() - t0_ml_warm) / iterations_ml * 1000.0
print(f"[7.1] ML predict_target_probability: Cold = {t_ml_prob_cold:.2f} ms | Warm = {t_ml_prob_warm:.3f} ms")

# 7.2 predict_supertrend_reversal
t0_rev_cold = time.perf_counter()
rev_prob_cold = predict_supertrend_reversal(
    candles_or_df=candle_list,
    confirmed_direction="BUY",
    horizon_minutes=5,
    current_price=candle_list[-1]["price"],
    supertrend_price=candle_list[-1]["price"] - 5.0,
    symbol="RELIANCE"
)
t_rev_cold = (time.perf_counter() - t0_rev_cold) * 1000.0

# 7.2.1 Cache-Hit inference
t0_rev_hit = time.perf_counter()
for _ in range(iterations_ml):
    rev_prob = predict_supertrend_reversal(
        candles_or_df=candle_list,
        confirmed_direction="BUY",
        horizon_minutes=5,
        current_price=candle_list[-1]["price"],
        supertrend_price=candle_list[-1]["price"] - 5.0,
        symbol="RELIANCE"
    )
t_rev_hit = (time.perf_counter() - t0_rev_hit) / iterations_ml * 1000.0

# 7.2.2 Cache-Miss inference (Fresh candle sequences requiring full feature extraction & scoring)
iterations_miss = 10
t0_rev_miss = time.perf_counter()
for step in range(iterations_miss):
    fresh_seq = list(candle_list)
    fresh_seq.append({
        "time": f"03:{31+step:02d} PM",
        "full_time": f"2026-10-15T15:{31+step:02d}:00",
        "price": round(candle_list[-1]["price"] + step * 0.5, 2),
        "open": round(candle_list[-1]["price"] + step * 0.5 - 0.2, 2),
        "high": round(candle_list[-1]["price"] + step * 0.5 + 0.8, 2),
        "low": round(candle_list[-1]["price"] + step * 0.5 - 0.8, 2),
        "close": round(candle_list[-1]["price"] + step * 0.5, 2),
        "volume": 6000
    })
    predict_supertrend_reversal(
        candles_or_df=fresh_seq,
        confirmed_direction="BUY",
        horizon_minutes=5,
        current_price=fresh_seq[-1]["price"],
        supertrend_price=fresh_seq[-1]["price"] - 5.0,
        symbol="RELIANCE"
    )
t_rev_miss = (time.perf_counter() - t0_rev_miss) / iterations_miss * 1000.0

print(f"[7.2] ML predict_supertrend_reversal: Cold = {t_rev_cold:.2f} ms | Cache-Hit = {t_rev_hit:.4f} ms | Cache-Miss (Full ML Scoring) = {t_rev_miss:.2f} ms")


# 2. Homepage Response Time
iterations_http = 10
t0_home = time.perf_counter()
for _ in range(iterations_http):
    r = session.get(f"{BASE_URL}/")
    assert r.status_code == 200
t_home = (time.perf_counter() - t0_home) / iterations_http
print(f"[2] Homepage (/) response time (avg of {iterations_http}): {t_home*1000:.2f} ms")

t0_live = time.perf_counter()
for _ in range(iterations_http):
    r = session.get(f"{BASE_URL}/live")
    assert r.status_code == 200
t_live = (time.perf_counter() - t0_live) / iterations_http
print(f"[2.1] Live page (/live) response time (avg of {iterations_http}): {t_live*1000:.2f} ms")

# 3. Initial Dashboard Loading Time (Simulating complete first-view sequence)
t0_dash = time.perf_counter()
for _ in range(iterations_http):
    session.get(f"{BASE_URL}/")
    session.get(f"{BASE_URL}/api/stocks")
    session.get(f"{BASE_URL}/api/system/settings")
    session.get(f"{BASE_URL}/api/notifications?limit=20")
t_dash = (time.perf_counter() - t0_dash) / iterations_http
print(f"[3] Initial Dashboard load suite (avg of {iterations_http}): {t_dash*1000:.2f} ms")

from app.api.auth import create_access_token
test_token = create_access_token({"sub": "1", "username": "user", "id": 1})
session.headers["Authorization"] = f"Bearer {test_token}"


# 8. Notification & History Loading Duration
t0_notif = time.perf_counter()
for _ in range(iterations_http):
    r = session.get(f"{BASE_URL}/api/notifications?limit=30")
    assert r.status_code == 200, f"Notification request failed: {r.status_code} {r.text}"
t_notif = (time.perf_counter() - t0_notif) / iterations_http
print(f"[8.1] Notification loading (/api/notifications?limit=30): {t_notif*1000:.2f} ms")

t0_hist = time.perf_counter()
for _ in range(iterations_http):
    r = session.get(f"{BASE_URL}/api/history?limit=30")
    assert r.status_code == 200, f"History request failed: {r.status_code} {r.text}"
t_hist = (time.perf_counter() - t0_hist) / iterations_http
print(f"[8.2] Alert History loading (/api/history?limit=30): {t_hist*1000:.2f} ms")


# 4. Stock Selection & Search API
t0_search = time.perf_counter()
for _ in range(iterations_http):
    r = session.get(f"{BASE_URL}/api/stocks/search?q=TCS")
    assert r.status_code == 200
t_search = (time.perf_counter() - t0_search) / iterations_http
print(f"[4.1] Stock search (/api/stocks/search?q=TCS): {t_search*1000:.2f} ms")

# 5. Market-data fetching duration (Cached vs Cached-Hit)
# Inject pre-cached mock to measure retrieval overhead
_RANGE_CACHE[("RELIANCE", "1D")] = {
    "data": {
        "symbol": "RELIANCE", "price": 2500.0, "status": "CLOSED",
        "candles": candle_list, "chart_points": candle_list
    },
    "timestamp": time.time()
}
t0_vis = time.perf_counter()
for _ in range(20):
    vis = get_stock_visualization_data("RELIANCE", "1D")
t_vis_cached = (time.perf_counter() - t0_vis) / 20.0
print(f"[5.1] Market-data visualization (cached 1D, avg of 20): {t_vis_cached*1000:.2f} ms")

t0_snap = time.perf_counter()
for _ in range(10):
    snap = get_latest_market_snapshot("RELIANCE")
t_snap_cached = (time.perf_counter() - t0_snap) / 10.0
print(f"[5.2] Market-data get_latest_market_snapshot (cached, avg of 10): {t_snap_cached*1000:.2f} ms")

# 10. Memory Usage
current_mem, peak_mem = tracemalloc.get_traced_memory()
tracemalloc.stop()
print(f"[10.1] Memory Current: {current_mem / 1024 / 1024:.2f} MB | Peak: {peak_mem / 1024 / 1024:.2f} MB")

# 11. Ten-Stock Monitoring Cycle Duration & Per-Symbol Latency
popular_10 = ["TCS", "INFY", "RELIANCE", "HDFCBANK", "SBIN", "ICICIBANK", "BHARTIARTL", "ITC", "WIPRO", "KOTAKBANK"]
t0_10 = time.perf_counter()
res_10 = scan_default_watchlist_supertrend(db, watchlist_symbols=popular_10, user_id=1)
t_10_cycle = (time.perf_counter() - t0_10) * 1000.0
print(f"[11.1] Ten-Stock Monitoring Cycle Duration: {t_10_cycle:.2f} ms")
from app.services.scheduler import _LAST_PER_SYMBOL_STATS
sample_timings = {k: v.get("duration_ms") for k, v in list(_LAST_PER_SYMBOL_STATS.items())[:4]}
print(f"[11.2] Sample Per-Symbol Latency (ms): {sample_timings}")

server.should_exit = True
db.close()
_cleanup_bench_db()
print("[Benchmark] Isolated database cleaned up successfully.")
print("=" * 70)
print("Benchmark complete.")
print("=" * 70)
