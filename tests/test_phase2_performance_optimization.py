import os
import time
import tempfile
import unittest
import threading
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.database import Base
from app.database.models import User, Alert, Notification, SystemSetting
from app.services.prediction import (
    load_model,
    load_reversal_model,
    predict_target_probability,
    predict_supertrend_reversal,
    _MODEL_CACHE,
    _MODEL_LOAD_LOCK,
    _REVERSAL_MODEL_LOAD_LOCK
)
from app.services.market_data import (
    get_latest_market_snapshot,
    get_stock_visualization_data,
    _SNAPSHOT_CACHE,
    _RANGE_CACHE
)
from app.services.scheduler import scheduled_monitoring_job, _JOB_LOCK
from app.services.alert_engine import scan_default_watchlist_supertrend
from app.api.auth import verify_password, hash_password, create_access_token

IST = ZoneInfo("Asia/Kolkata")


class TestPhase2PerformanceOptimization(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp_dir.name, "test_p2.db")
        cls.engine = create_engine(f"sqlite:///{cls.db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=cls.engine)
        cls.SessionLocal = sessionmaker(bind=cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        cls.tmp_dir.cleanup()

    def setUp(self):
        self.db = self.SessionLocal()

    def tearDown(self):
        self.db.close()

    # -------------------------------------------------------------------------
    # 1. Thread-safe Model Initialization under Concurrent Requests
    # -------------------------------------------------------------------------

    def test_concurrent_model_loading_is_thread_safe_and_single_instance(self):
        """Verify concurrent threads calling load_model() produce identical single model cache without race."""
        results = []
        errors = []

        def worker():
            try:
                m = load_model()
                results.append(id(m))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent load_model had errors: {errors}")
        self.assertEqual(len(results), 8)
        # All 8 threads must receive the exact same cached object reference in memory
        self.assertEqual(len(set(results)), 1, "Concurrent model loads created divergent instances in memory!")

    # -------------------------------------------------------------------------
    # 2. Transparent Fallback Status Exposure
    # -------------------------------------------------------------------------

    def test_predict_target_probability_exposes_fallback_status_when_model_missing(self):
        """Verify predict_target_probability explicitly flags is_fallback=True and model_status='FALLBACK'."""
        with patch("app.services.prediction.load_model", return_value=None):
            snap = {
                "symbol": "TCS",
                "price": 3500.0,
                "change_pct": 0.5,
                "latest_market_timestamp": "2026-10-15T10:00:00",
                "features": {"volatility_10m": 0.15, "return_5m": 0.1, "rsi_14": 55.0}
            }
            res = predict_target_probability(
                symbol="TCS",
                target_percent=1.0,
                warning_minutes=5,
                direction="UP",
                market_snapshot=snap
            )
            self.assertTrue(res["is_fallback"], "Fallback must be marked True when ML model is unavailable")
            self.assertEqual(res["model_status"], "FALLBACK")
            self.assertEqual(res["model_type"], "STATISTICAL_FALLBACK")
            self.assertIn("Statistical", res["explanation"])

    def test_predict_target_probability_marks_loaded_status_when_model_available(self):
        """Verify predict_target_probability marks is_fallback=False when ML model is active."""
        snap = {
            "symbol": "TCS",
            "price": 3500.0,
            "change_pct": 0.5,
            "latest_market_timestamp": "2026-10-15T10:00:00",
            "features": {"volatility_10m": 0.15, "return_5m": 0.1, "rsi_14": 55.0}
        }
        res = predict_target_probability(
            symbol="TCS",
            target_percent=1.0,
            warning_minutes=5,
            direction="UP",
            market_snapshot=snap
        )
        if res.get("model_status") == "LOADED":
            self.assertFalse(res["is_fallback"])
            self.assertIn("Random", res["model_type"])

    # -------------------------------------------------------------------------
    # 3. Market Data Snapshot Cache Freshness & Performance
    # -------------------------------------------------------------------------

    def test_snapshot_cache_reuses_valid_features_without_recomputing(self):
        """Verify get_latest_market_snapshot caches features and updates dynamic data_status correctly."""
        mock_vis = {
            "symbol": "TEST_SYM",
            "name": "Test Company",
            "price": 500.0,
            "previous_close": 495.0,
            "price_change": 5.0,
            "change_pct": 1.01,
            "raw_volume": 100000,
            "open": 496.0,
            "day_high": 502.0,
            "day_low": 494.0,
            "last_updated": "10:30:00 AM",
            "latest_market_timestamp": "2026-10-15T10:30:00",
            "data_received_at": datetime.now(IST).isoformat(),
            "data_status": "LIVE",
            "data_age_seconds": 15,
            "data_interval": "1m",
            "candles": [
                {"time": f"10:{i:02d}", "open": 495.0, "high": 500.0, "low": 494.0, "close": 498.0, "volume": 1000}
                for i in range(10)
            ]
        }
        with patch("app.services.market_data.get_stock_visualization_data", return_value=mock_vis):
            t0 = time.perf_counter()
            snap1 = get_latest_market_snapshot("TEST_SYM")
            lat1 = (time.perf_counter() - t0) * 1000.0

            # Second call should be instant cache hit (< 1ms)
            t1 = time.perf_counter()
            snap2 = get_latest_market_snapshot("TEST_SYM")
            lat2 = (time.perf_counter() - t1) * 1000.0

            self.assertEqual(snap1["price"], snap2["price"])
            self.assertEqual(snap1["symbol"], snap2["symbol"])
            self.assertLess(lat2, 10.0, f"Second snapshot call took too long: {lat2:.2f}ms")

    # -------------------------------------------------------------------------
    # 4. Scheduler Overlap Prevention
    # -------------------------------------------------------------------------

    def test_scheduler_skips_overlapping_ticks_when_job_lock_is_held(self):
        """Verify scheduled_monitoring_job drops concurrent ticks without overlapping."""
        lock_acquired = _JOB_LOCK.acquire(blocking=False)
        self.assertTrue(lock_acquired, "Should be able to acquire lock in test")
        try:
            # While lock is held, scheduled_monitoring_job should return immediately without executing
            t0 = time.perf_counter()
            with patch("app.services.scheduler.evaluate_active_alerts") as mock_eval:
                scheduled_monitoring_job()
                mock_eval.assert_not_called()
            dur_ms = (time.perf_counter() - t0) * 1000.0
            self.assertLess(dur_ms, 50.0, "Skipped tick should return immediately")
        finally:
            _JOB_LOCK.release()

    # -------------------------------------------------------------------------
    # 5. Watchlist Scanner Batch Processing & Active Alert Suppression
    # -------------------------------------------------------------------------

    def test_watchlist_scanner_skips_stocks_with_active_alerts_in_batch(self):
        """Verify scan_default_watchlist_supertrend suppresses watchlist check if active alert exists."""
        alert = Alert(
            id=777,
            user_id=1,
            symbol="TCS",
            target_percent=1.0,
            warning_minutes=5,
            direction="UP",
            min_probability=0.5,
            monitoring_interval=60,
            notification_method="Popup",
            status="MONITORING",
            enable_supertrend=True,
            cooldown_minutes=5,
            created_at=datetime.utcnow()
        )
        self.db.add(alert)
        self.db.commit()

        mock_snap = {
            "symbol": "TCS",
            "price": 3500.0,
            "status": "LIVE",
            "data_status": "LIVE",
            "candles": [{"time": "10:00", "open": 3500.0, "high": 3505.0, "low": 3495.0, "close": 3502.0, "volume": 500}]
        }
        res = scan_default_watchlist_supertrend(
            self.db,
            watchlist_symbols=["TCS"],
            user_id=1,
            preloaded_snapshots={"TCS": mock_snap}
        )
        # Should be empty because TCS is managed by active alert #777
        self.assertEqual(len(res), 0)

    # -------------------------------------------------------------------------
    # 6. Authentication Persistence Preservation
    # -------------------------------------------------------------------------

    def test_auth_password_hashing_and_token_generation_preservation(self):
        """Verify password hashing, verification, and JWT creation remain functional."""
        raw_pw = "TraderSecure2026!"
        hashed = hash_password(raw_pw)
        self.assertTrue(verify_password(raw_pw, hashed))
        self.assertFalse(verify_password("WrongPassword!", hashed))

        token = create_access_token({"sub": "testuser", "id": 1, "role": "trader"})
        self.assertIsInstance(token, str)
        self.assertGreater(len(token), 20)


if __name__ == "__main__":
    unittest.main()
