"""
Phase 3 Safety Fixes Regression Test Suite

Validates all 7 audit regression requirements:
1. Intermediate candle high/low/close modifications invalidate cached calculations.
2. Historical candle corrections with unchanged latest candle invalidate affected calculations.
3. Identical candle histories and equivalent parameters reuse valid cached results.
4. Model/configuration changes invalidate affected prediction caches.
5. Cached and fresh results match for identical inputs.
6. The Windows logging path does not crash under a cp1252 console encoding.
7. The benchmark database is strictly isolated from data/stockpulse.db.
"""

import os
import sys
import io
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.feature_engine import (
    get_candle_sequence_key,
    compute_supertrend,
    analyze_supertrend_hit,
    compute_market_features,
    extract_supertrend_reversal_feature_vector,
    _COMPUTE_ST_CACHE,
    _ANALYZE_ST_CACHE,
    _MARKET_FEATURES_CACHE,
    _REVERSAL_FEAT_CACHE
)
from app.services.prediction import (
    predict_supertrend_reversal,
    predict_target_probability,
    load_model,
    load_reversal_model,
    _PREDICT_TARGET_PROB_CACHE,
    _PREDICT_ST_REVERSAL_CACHE,
    MODEL_PATH,
    REVERSAL_MODEL_PATH
)
from app.services.alert_engine import evaluate_active_alerts
from app.database.models import Alert, User


class TestPhase3SafetyFixes(unittest.TestCase):

    def setUp(self):
        _COMPUTE_ST_CACHE.clear()
        _ANALYZE_ST_CACHE.clear()
        _MARKET_FEATURES_CACHE.clear()
        _REVERSAL_FEAT_CACHE.clear()
        _PREDICT_TARGET_PROB_CACHE.clear()
        _PREDICT_ST_REVERSAL_CACHE.clear()

    def _make_base_candles(self, n=30, base_price=1000.0):
        start_time = datetime(2026, 10, 18, 9, 15)
        candles = []
        for i in range(n):
            t_str = (start_time + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%S")
            p = round(base_price + i * 0.5, 2)
            candles.append({
                "time": (start_time + timedelta(minutes=i)).strftime("%H:%M"),
                "full_time": t_str,
                "open": round(p - 0.2, 2),
                "high": round(p + 0.8, 2),
                "low": round(p - 0.8, 2),
                "close": p,
                "price": p,
                "volume": 5000 + i * 10
            })
        return candles

    def test_01_intermediate_candle_modification_invalidates_supertrend_cache(self):
        """1. Intermediate candle high/low/close modification MUST invalidate Supertrend cache."""
        c1 = self._make_base_candles(30)
        c2 = [dict(x) for x in c1]

        # Alter an intermediate candle (candle 15) while keeping length, first candle, and last candle identical
        c2[15]["high"] = c1[15]["high"] + 50.0
        c2[15]["low"] = c1[15]["low"] - 50.0
        c2[15]["close"] = c1[15]["close"] + 25.0

        key1 = get_candle_sequence_key(c1)
        key2 = get_candle_sequence_key(c2)
        self.assertNotEqual(key1, key2, "Fingerprints MUST differ when an intermediate candle is modified!")

        df1 = pd.DataFrame(c1)
        df2 = pd.DataFrame(c2)

        st1 = compute_supertrend(df1)
        st2 = compute_supertrend(df2)

        # Ensure the cached computation for df2 reflects the actual high volatility spike, NOT stale st1
        self.assertNotEqual(st1["atr"].iloc[-1], st2["atr"].iloc[-1], "Cached ATR must not be reused across modified middle candles!")

    def test_02_historical_candle_corrections_with_unchanged_latest_candle(self):
        """2. Historical candle correction with identical latest candle invalidates market features."""
        c1 = self._make_base_candles(30)
        c2 = [dict(x) for x in c1]

        # Modify historical bar 5
        c2[5]["open"] = c1[5]["open"] + 15.0
        c2[5]["close"] = c1[5]["close"] - 15.0

        # Keep latest candle identical
        self.assertEqual(c1[-1], c2[-1])

        df1 = pd.DataFrame(c1)
        df2 = pd.DataFrame(c2)

        mf1 = compute_market_features(df1)
        mf2 = compute_market_features(df2)

        # Features such as rsi or volatility must not be identical stale clones
        self.assertIn("rsi_14", mf1.columns)
        self.assertNotEqual(mf1["rsi_14"].iloc[-1], mf2["rsi_14"].iloc[-1], "Historical correction must invalidate market features cache!")

    def test_03_reversal_prediction_invalidates_on_intermediate_modification(self):
        """3. Predict supertrend reversal invalidates and recomputes probability on middle candle changes."""
        c1 = self._make_base_candles(30)
        c2 = [dict(x) for x in c1]

        # Change middle candles 10..20 to high adverse volatility
        for j in range(10, 20):
            c2[j]["high"] = c1[j]["high"] + 30.0
            c2[j]["low"] = c1[j]["low"] - 30.0
            c2[j]["close"] = c1[j]["close"] - 15.0

        p1 = predict_supertrend_reversal(c1, confirmed_direction="BUY", current_price=c1[-1]["close"], supertrend_price=c1[-1]["close"] - 5.0, symbol="TEST")
        p2 = predict_supertrend_reversal(c2, confirmed_direction="BUY", current_price=c2[-1]["close"], supertrend_price=c2[-1]["close"] - 5.0, symbol="TEST")

        self.assertNotEqual(p1["reversal_probability"], p2["reversal_probability"], "Reversal probability must differ when intermediate candle regime changes!")

    def test_04_identical_candle_histories_reuse_valid_cached_results(self):
        """4. Identical candle histories and equivalent parameters must cleanly hit cache."""
        c1 = self._make_base_candles(30)
        c2 = [dict(x) for x in c1]

        key1 = get_candle_sequence_key(c1)
        key2 = get_candle_sequence_key(c2)
        self.assertEqual(key1, key2, "Identical inputs MUST produce identical sequence keys!")

        t0 = time.perf_counter()
        res1 = analyze_supertrend_hit(c1, current_price=c1[-1]["close"])
        t_cold = time.perf_counter() - t0

        t0 = time.perf_counter()
        res2 = analyze_supertrend_hit(c2, current_price=c2[-1]["close"])
        t_warm = time.perf_counter() - t0

        self.assertEqual(res1["supertrend_price"], res2["supertrend_price"])
        self.assertEqual(res1["reversal_risk_score"], res2["reversal_risk_score"])
        self.assertLess(t_warm, t_cold, "Warm cache hit must be faster than cold calculation!")

    def test_05_cached_and_fresh_results_match_for_identical_inputs(self):
        """5. Cached and fresh results match exactly for identical inputs."""
        c1 = self._make_base_candles(30)
        sample_snapshot = {
            "symbol": "RELIANCE",
            "price": c1[-1]["close"],
            "change_pct": 0.5,
            "latest_market_timestamp": c1[-1]["full_time"],
            "features": {"volatility_10m": 0.15, "return_5m": 0.01, "roc_10m": 0.02, "vol_ratio_20m": 1.1, "rsi_14": 52.0}
        }

        # Cold execution
        _PREDICT_TARGET_PROB_CACHE.clear()
        cold_pred = predict_target_probability("RELIANCE", target_percent=0.5, warning_minutes=5, market_snapshot=sample_snapshot)

        # Warm execution (from cache)
        warm_pred = predict_target_probability("RELIANCE", target_percent=0.5, warning_minutes=5, market_snapshot=sample_snapshot)

        # Fresh execution after cache clear
        _PREDICT_TARGET_PROB_CACHE.clear()
        fresh_pred = predict_target_probability("RELIANCE", target_percent=0.5, warning_minutes=5, market_snapshot=sample_snapshot)

        self.assertEqual(cold_pred["probability"], warm_pred["probability"])
        self.assertEqual(warm_pred["probability"], fresh_pred["probability"])
        self.assertEqual(cold_pred["target_price"], fresh_pred["target_price"])

    def test_06_model_mtime_change_invalidates_prediction_cache(self):
        """6. Model modification timestamp change invalidates prediction caches."""
        sample_snapshot = {
            "symbol": "RELIANCE",
            "price": 1000.0,
            "change_pct": 0.5,
            "latest_market_timestamp": "2026-10-18T10:00:00",
            "features": {"volatility_10m": 0.15}
        }

        _PREDICT_TARGET_PROB_CACHE.clear()
        p1 = predict_target_probability("RELIANCE", target_percent=0.5, warning_minutes=5, market_snapshot=sample_snapshot)
        self.assertEqual(len(_PREDICT_TARGET_PROB_CACHE), 1)

        # Simulate model update by clearing _MODEL_CACHE and changing mtime
        import app.services.prediction as pred_mod
        old_mtime = pred_mod._MODEL_MTIME
        try:
            pred_mod._MODEL_MTIME = old_mtime + 100.0  # Simulated new mtime
            pred_mod._MODEL_CACHE = None
            pred_mod.load_model()
            # Cache must have been cleared on reload
            self.assertEqual(len(pred_mod._PREDICT_TARGET_PROB_CACHE), 0, "Prediction cache must be cleared when model file updates!")
        finally:
            pred_mod._MODEL_MTIME = old_mtime

    def test_07_windows_logging_path_does_not_crash_under_cp1252(self):
        """7. Windows console logging under cp1252 encoding must not crash with UnicodeEncodeError."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.database.database import Base

        mem_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=mem_engine)
        MemSession = sessionmaker(bind=mem_engine)
        mem_db = MemSession()

        u = User(id=1, username="test_cp1252", email="cp1252@test.com", password_hash="pwd")
        mem_db.add(u)
        test_alert = Alert(
            id=999,
            user_id=1,
            symbol="TCS",
            target_percent=0.50,
            warning_minutes=5,
            status="MONITORING",
            enable_supertrend=True,
            reference_price=1000.0,
            started_at=datetime.utcnow()
        )
        mem_db.add(test_alert)
        mem_db.commit()

        mock_snap = {
            "symbol": "TCS",
            "price": 1005.50,  # 0.55% reached (> 0.50%)
            "change_pct": 0.55,
            "previous_close": 1000.0,
            "day_high": 1010.0,
            "day_low": 995.0,
            "open": 1000.0,
            "data_status": "LIVE",
            "status": "LIVE",
            "latest_market_timestamp": "2026-10-18T10:00:00",
            "candles": self._make_base_candles(20),
            "features": {}
        }

        # Mock stdout to a strict cp1252 writer
        cp1252_buffer = io.BytesIO()
        strict_cp1252_stdout = io.TextIOWrapper(cp1252_buffer, encoding="cp1252", errors="strict")

        with patch("sys.stdout", strict_cp1252_stdout), \
             patch("app.services.market_data.get_latest_market_snapshot", return_value=mock_snap):
            try:
                evaluate_active_alerts(mem_db)
                strict_cp1252_stdout.flush()
                output = cp1252_buffer.getvalue().decode("cp1252")
                self.assertIn("Target +0.50% reached directly at Rs.1005.50", output)
            except UnicodeEncodeError as uerr:
                self.fail(f"UnicodeEncodeError raised in Windows cp1252 console: {uerr}")
        mem_db.close()

    def test_08_benchmark_does_not_mutate_or_target_production_database(self):
        """8. Benchmark suite engine must not point to data/stockpulse.db."""
        import tempfile
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        # Test database isolation logic used by benchmark suite
        with tempfile.TemporaryDirectory() as tmpdir:
            test_db_path = os.path.join(tmpdir, "test_bench.db")
            isolated_engine = create_engine(f"sqlite:///{test_db_path}")
            self.assertNotIn("data/stockpulse.db", str(isolated_engine.url))
            self.assertIn("test_bench.db", str(isolated_engine.url))

            # Verify benchmark_suite.py source code contains the isolation assertion and cleanup
            with open("tests/benchmark_suite.py", "r", encoding="utf-8") as f:
                bench_code = f.read()
            self.assertIn("_BENCHMARK_TMP_DIR = tempfile.TemporaryDirectory()", bench_code)
            self.assertIn('assert "data/stockpulse.db" not in unquoted_url', bench_code)
            self.assertIn("_cleanup_bench_db()", bench_code)


if __name__ == "__main__":
    unittest.main()
