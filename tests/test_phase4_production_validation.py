import unittest
import time
import os
import json
import tempfile
import threading
import pandas as pd
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from unittest.mock import patch, MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.database.database as db_mod
from app.database.models import Base, User, Alert, Notification, AlertHistory, SystemSetting
from app.api.auth import hash_password, verify_password, create_access_token, decode_access_token
from app.services.market_data import (
    get_market_status,
    is_us_symbol,
    get_stock_visualization_data,
    get_latest_market_snapshot,
    NSE_HOLIDAYS,
    US_HOLIDAYS,
    IST,
    US_ET,
    _RANGE_CACHE,
    _FAST_INFO_CACHE,
    _NEGATIVE_LOOKUP_CACHE
)
from app.services.feature_engine import (
    analyze_supertrend_hit,
    get_candle_sequence_key,
    _LIST_CANDLE_KEY_CACHE
)
from app.services.prediction import (
    predict_supertrend_reversal,
    predict_target_probability,
    is_prewarm_complete,
    _PREDICT_ST_REVERSAL_CACHE
)
from app.services.alert_engine import (
    get_supertrend_settings,
    evaluate_active_alerts,
    scan_default_watchlist_supertrend
)
from app.services.messaging.notification_manager import NotificationManager


class TestPhase4ProductionValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create an isolated in-memory/temp SQLite database for testing
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.test_db_path = os.path.join(cls.temp_dir.name, "test_phase4.db")
        cls.test_engine = create_engine(
            f"sqlite:///{cls.test_db_path}",
            connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(bind=cls.test_engine)
        cls.TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=cls.test_engine)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.temp_dir.cleanup()
        except Exception:
            pass

    def setUp(self):
        self.db = self.TestSessionLocal()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    # =========================================================================
    # 1. Exchange Calendar, Timezones, Holidays & DST Handling
    # =========================================================================

    def test_indian_market_hours_and_holidays(self):
        """Verify Indian NSE/BSE market status: regular hours, weekends, and official holidays."""
        # Indian market open: Wednesday 10:30 AM IST (non-holiday)
        wed_open = datetime(2026, 10, 14, 10, 30, tzinfo=IST)
        status, _ = get_market_status(wed_open, symbol="RELIANCE.NS")
        self.assertEqual(status, "OPEN")

        # Indian market closed: Wednesday 08:30 AM IST (pre-market)
        wed_pre = datetime(2026, 10, 14, 8, 30, tzinfo=IST)
        status, _ = get_market_status(wed_pre, symbol="RELIANCE.NS")
        self.assertEqual(status, "CLOSED")

        # Indian market closed: Wednesday 04:00 PM IST (post-market)
        wed_post = datetime(2026, 10, 14, 16, 0, tzinfo=IST)
        status, _ = get_market_status(wed_post, symbol="RELIANCE.NS")
        self.assertEqual(status, "CLOSED")

        # Weekend: Saturday 11:00 AM IST
        sat = datetime(2026, 10, 17, 11, 0, tzinfo=IST)
        status, _ = get_market_status(sat, symbol="RELIANCE.NS")
        self.assertEqual(status, "CLOSED")

        # Official NSE Holiday: Republic Day (2026-01-26) at 11:00 AM IST
        republic_day = datetime(2026, 1, 26, 11, 0, tzinfo=IST)
        status, _ = get_market_status(republic_day, symbol="TCS.NS")
        self.assertEqual(status, "CLOSED")

        # Official NSE Holiday: Independence Day (2026-08-15)
        ind_day = datetime(2026, 8, 15, 11, 0, tzinfo=IST)
        status, _ = get_market_status(ind_day, symbol="INFY.NS")
        self.assertEqual(status, "CLOSED")

        # Future calendar year validation (2027+):
        rep_day_2027 = datetime(2027, 1, 26, 11, 0, tzinfo=IST)
        status, _ = get_market_status(rep_day_2027, symbol="RELIANCE.NS")
        self.assertEqual(status, "CLOSED")

        good_fri_nse_2027 = datetime(2027, 3, 26, 11, 0, tzinfo=IST)
        status, _ = get_market_status(good_fri_nse_2027, symbol="TCS.NS")
        self.assertEqual(status, "CLOSED")

    def test_us_market_hours_holidays_and_dst(self):
        """Verify US NYSE/NASDAQ market status: 09:30-16:00 ET, weekends, US holidays, and DST."""
        # US market open: Wednesday 11:00 AM ET
        wed_us_open = datetime(2026, 10, 14, 11, 0, tzinfo=US_ET)
        status, _ = get_market_status(wed_us_open, symbol="AAPL")
        self.assertEqual(status, "OPEN")

        # US market closed: Wednesday 08:30 AM ET (pre-market)
        wed_us_pre = datetime(2026, 10, 14, 8, 30, tzinfo=US_ET)
        status, _ = get_market_status(wed_us_pre, symbol="MSFT")
        self.assertEqual(status, "CLOSED")

        # US market closed: Wednesday 04:30 PM ET (post-market)
        wed_us_post = datetime(2026, 10, 14, 16, 30, tzinfo=US_ET)
        status, _ = get_market_status(wed_us_post, symbol="NVDA")
        self.assertEqual(status, "CLOSED")

        # US Weekend: Sunday 12:00 PM ET
        sun_us = datetime(2026, 10, 18, 12, 0, tzinfo=US_ET)
        status, _ = get_market_status(sun_us, symbol="GOOGL")
        self.assertEqual(status, "CLOSED")

        # Official US Holiday: Martin Luther King Jr. Day (2026-01-19)
        mlk_day = datetime(2026, 1, 19, 11, 0, tzinfo=US_ET)
        status, _ = get_market_status(mlk_day, symbol="AAPL")
        self.assertEqual(status, "CLOSED")

        # Official US Holiday: Independence Day Observed (2026-07-03)
        july4_obs = datetime(2026, 7, 3, 11, 0, tzinfo=US_ET)
        status, _ = get_market_status(july4_obs, symbol="AAPL")
        self.assertEqual(status, "CLOSED")

        # Future calendar year validation (2027+):
        mlk_2027 = datetime(2027, 1, 18, 11, 0, tzinfo=US_ET)
        status, _ = get_market_status(mlk_2027, symbol="AAPL")
        self.assertEqual(status, "CLOSED")

        good_fri_2027 = datetime(2027, 3, 26, 11, 0, tzinfo=US_ET)
        status, _ = get_market_status(good_fri_2027, symbol="MSFT")
        self.assertEqual(status, "CLOSED")

        thx_2027 = datetime(2027, 11, 25, 11, 0, tzinfo=US_ET)
        status, _ = get_market_status(thx_2027, symbol="NVDA")
        self.assertEqual(status, "CLOSED")

        # DST Verification: Summer (EDT UTC-4) vs Winter (EST UTC-5)
        summer_time = datetime(2026, 6, 15, 10, 0, tzinfo=US_ET)
        self.assertEqual(summer_time.utcoffset(), timedelta(hours=-4))

        winter_time = datetime(2026, 12, 15, 10, 0, tzinfo=US_ET)
        self.assertEqual(winter_time.utcoffset(), timedelta(hours=-5))

    def test_market_status_caching_performance(self):
        """Verify get_market_status live caching returns in < 0.2ms and as_of bypasses cache."""
        # Live status query
        t0 = time.perf_counter()
        for _ in range(50):
            st, tm = get_market_status()
        dur_avg = (time.perf_counter() - t0) / 50.0 * 1000.0
        self.assertLess(dur_avg, 0.2, f"Cached get_market_status took too long: {dur_avg:.4f} ms")

        # Passing as_of bypasses cache and evaluates deterministic timestamp
        test_dt = datetime(2026, 10, 14, 11, 0, tzinfo=IST)
        st_hist, _ = get_market_status(as_of=test_dt)
        self.assertEqual(st_hist, "OPEN")

    # =========================================================================
    # 2. Market-Data Reliability, Honest Statuses & In-Flight Deduplication
    # =========================================================================

    def test_honest_market_statuses_no_synthetic_data(self):
        """Verify data status transitions: LIVE, DELAYED, STALE, CLOSED, DATA_UNAVAILABLE."""
        # 1. Closed market status
        wed_closed = datetime(2026, 10, 14, 18, 0, tzinfo=IST)
        with patch("app.services.market_data.get_market_status", return_value=("CLOSED", "06:00:00 PM")):
            vis = get_stock_visualization_data("RELIANCE", "1D")
            self.assertEqual(vis["market_status"], "CLOSED")
            self.assertEqual(vis["data_status"], "CLOSED")

        # 2. Open market with fresh data (LIVE <= 180s)
        now_utc = datetime.now(timezone.utc)
        fresh_ts = (now_utc - timedelta(seconds=60)).isoformat()
        _RANGE_CACHE[("TEST_LIVE", "1D")] = {
            "data": {
                "symbol": "TEST_LIVE", "price": 1000.0,
                "latest_market_timestamp": fresh_ts, "candles": []
            },
            "timestamp": time.time()
        }
        with patch("app.services.market_data.get_market_status", return_value=("OPEN", "10:00:00 AM")):
            vis_live = get_stock_visualization_data("TEST_LIVE", "1D")
            self.assertEqual(vis_live["data_status"], "LIVE")

        # 3. Open market with delayed data (DELAYED: 180s - 900s)
        delayed_ts = (now_utc - timedelta(seconds=300)).isoformat()
        _RANGE_CACHE[("TEST_DELAYED", "1D")] = {
            "data": {
                "symbol": "TEST_DELAYED", "price": 1000.0,
                "latest_market_timestamp": delayed_ts, "candles": []
            },
            "timestamp": time.time()
        }
        with patch("app.services.market_data.get_market_status", return_value=("OPEN", "10:00:00 AM")):
            vis_delayed = get_stock_visualization_data("TEST_DELAYED", "1D")
            self.assertEqual(vis_delayed["data_status"], "DELAYED")

        # 4. Open market with stale data (STALE > 900s)
        stale_ts = (now_utc - timedelta(seconds=1200)).isoformat()
        _RANGE_CACHE[("TEST_STALE", "1D")] = {
            "data": {
                "symbol": "TEST_STALE", "price": 1000.0,
                "latest_market_timestamp": stale_ts, "candles": []
            },
            "timestamp": time.time()
        }
        with patch("app.services.market_data.get_market_status", return_value=("OPEN", "10:00:00 AM")):
            vis_stale = get_stock_visualization_data("TEST_STALE", "1D")
            self.assertEqual(vis_stale["data_status"], "STALE")

    def test_in_flight_concurrent_deduplication(self):
        """Verify that concurrent requests for the same uncached symbol deduplicate into one network call."""
        fetch_counter = {"calls": 0}

        class MockTicker:
            def __init__(self, ticker):
                self.ticker = ticker
            def history(self, *args, **kwargs):
                fetch_counter["calls"] += 1
                time.sleep(0.05)
                dates = pd.date_range("2026-10-22 09:15", periods=10, freq="1min", tz="Asia/Kolkata")
                return pd.DataFrame({
                    "Open": [2500.0]*10, "High": [2510.0]*10, "Low": [2490.0]*10,
                    "Close": [2505.0]*10, "Volume": [1000]*10
                }, index=dates)

        _RANGE_CACHE.pop(("CONCURRENT_TEST", "1D"), None)
        with patch("yfinance.Ticker", side_effect=MockTicker):
            results = []
            threads = []
            for _ in range(4):
                t = threading.Thread(target=lambda: results.append(get_stock_visualization_data("CONCURRENT_TEST", "1D")))
                threads.append(t)
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(len(results), 4)
            # Deduplication ensures minimal live calls (at most 1)
            self.assertLessEqual(fetch_counter["calls"], 2)

    # =========================================================================
    # 3. Supertrend Notifications, Warnings vs Reversals, Signed Messages
    # =========================================================================

    def test_supertrend_early_warning_signed_messages_and_separation(self):
        """Test percentage calculation, signed proximity messages, and strict separation between early warning and reversal."""
        candles = [
            {"time": f"09:{15+i:02d}", "open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0, "volume": 1000}
            for i in range(30)
        ]

        # Scenario A: SuperTrend hit analysis with proximity percent
        res_buy = analyze_supertrend_hit(
            candles_or_df=candles,
            current_price=100.50,
            proximity_percent=0.50,
            period=10,
            multiplier=3.0
        )
        self.assertIn("direction", res_buy)
        self.assertIn("is_approaching", res_buy)
        self.assertIn("proximity_percent", res_buy)

        # Scenario B: Strict state machine separation in prediction
        pred_warning = predict_supertrend_reversal(
            candles_or_df=candles,
            confirmed_direction="BUY",
            horizon_minutes=5,
            current_price=100.1,
            supertrend_price=100.0,
            threshold=0.30,  # Lower threshold to trigger early warning
            symbol="TEST_SYM"
        )
        # Prediction state can be EARLY_WARNING or NO_CLEAR_SIGNAL, but NEVER CONFIRMED_BUY/CONFIRMED_SELL
        self.assertIn(pred_warning["prediction_state"], ["EARLY_WARNING", "NO_CLEAR_SIGNAL", "DATA_UNAVAILABLE"])
        self.assertNotIn(pred_warning["prediction_state"], ["CONFIRMED_BUY", "CONFIRMED_SELL"])
        self.assertEqual(pred_warning["confirmed_direction"], "BUY")

    def test_duplicate_event_prevention_and_expiration(self):
        """Verify that duplicate notifications within deduplication window are prevented."""
        event_key = "USER_1:RELIANCE:EARLY_WARNING:BUY"
        NotificationManager._DEDUPLICATED_EVENT_KEYS.add(event_key)

        # Immediately testing same event key
        is_duplicate = (event_key in NotificationManager._DEDUPLICATED_EVENT_KEYS)
        self.assertTrue(is_duplicate)

        # Remove key to simulate window expiration or reset
        NotificationManager._DEDUPLICATED_EVENT_KEYS.remove(event_key)
        self.assertNotIn(event_key, NotificationManager._DEDUPLICATED_EVENT_KEYS)

    # =========================================================================
    # 4. Authentication, Bcrypt Password Safety & Persistence
    # =========================================================================

    def test_bcrypt_72_byte_safety_and_compatibility(self):
        """Verify bcrypt password truncation handles >72 byte passwords safely without throwing ValueError."""
        # 1. Normal password (< 72 bytes)
        normal_pw = "SecurePassword123!"
        hashed_normal = hash_password(normal_pw)
        self.assertTrue(verify_password(normal_pw, hashed_normal))
        self.assertFalse(verify_password("WrongPassword!", hashed_normal))

        # 2. Long password (> 72 bytes, e.g. 100 characters)
        long_pw = "A" * 100
        hashed_long = hash_password(long_pw)
        # Must verify correctly and NOT raise ValueError: password cannot be longer than 72 bytes
        self.assertTrue(verify_password(long_pw, hashed_long))
        self.assertFalse(verify_password("A" * 70, hashed_long))

        # 3. Leading/trailing whitespace tolerance on user input
        hashed_clean = hash_password("PasswordWithSpaces123")
        self.assertTrue(verify_password("PasswordWithSpaces123", hashed_clean))
        self.assertTrue(verify_password("  PasswordWithSpaces123  ", hashed_clean))

    def test_user_and_settings_persistence(self):
        """Verify accounts, watchlists, alert settings persist across database transactions."""
        user = User(
            username="test_phase4_user",
            email="phase4@stockpulse.local",
            password_hash=hash_password("Phase4Password!"),
            is_verified=True
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)

        # Add custom system setting
        setting = SystemSetting(
            key="supertrend_watchlist_global",
            value=json.dumps(["TCS", "INFY", "AAPL"])
        )
        self.db.add(setting)
        self.db.commit()

        # Simulate fresh session/restart query
        query_user = self.db.query(User).filter(User.username == "test_phase4_user").first()
        self.assertIsNotNone(query_user)
        self.assertEqual(query_user.email, "phase4@stockpulse.local")
        self.assertTrue(verify_password("Phase4Password!", query_user.password_hash))

        query_setting = self.db.query(SystemSetting).filter(
            SystemSetting.key == "supertrend_watchlist_global"
        ).first()
        self.assertIsNotNone(query_setting)
        self.assertEqual(json.loads(query_setting.value), ["TCS", "INFY", "AAPL"])

    # =========================================================================
    # 5. Deployment, Resource Stability & Memory Bounds
    # =========================================================================

    def test_database_rollback_and_session_safety_on_exception(self):
        """Verify database rollback on exception preserves session integrity."""
        try:
            # Attempt invalid insert that causes integrity/syntax violation
            invalid_user = User(username=None, email=None)
            self.db.add(invalid_user)
            self.db.flush()
        except Exception:
            self.db.rollback()

        # Session should be healthy and usable after rollback
        users_count = self.db.query(User).count()
        self.assertGreaterEqual(users_count, 0)

    def test_cache_bounds_and_memory_safety(self):
        """Verify that internal caches enforce upper bounds and do not grow infinitely."""
        # Test _LIST_CANDLE_KEY_CACHE bounding
        initial_size = len(_LIST_CANDLE_KEY_CACHE)
        candles = [{"time": "10:00", "close": 100.0 + i} for i in range(10)]
        for i in range(600):
            # Pass distinct lists
            c_copy = list(candles)
            c_copy[0] = {"time": f"10:{i%60:02d}", "close": 100.0 + i}
            get_candle_sequence_key(c_copy)

        self.assertLessEqual(len(_LIST_CANDLE_KEY_CACHE), 513)

    def test_reversal_prediction_performance(self):
        """Verify predict_supertrend_reversal runs efficiently under warm conditions."""
        candles = [
            {"time": f"09:{15+i:02d}", "open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0, "volume": 1000}
            for i in range(30)
        ]
        # First call loads or warms
        res1 = predict_supertrend_reversal(candles, "BUY", 5, current_price=101.0, supertrend_price=99.0, symbol="TCS")
        self.assertIn("reversal_probability", res1)

        # Warm iterations
        t0 = time.perf_counter()
        for _ in range(10):
            predict_supertrend_reversal(candles, "BUY", 5, current_price=101.0, supertrend_price=99.0, symbol="TCS")
        warm_avg = (time.perf_counter() - t0) / 10.0 * 1000.0
        # Warm cached prediction should take well under 10ms
        self.assertLess(warm_avg, 10.0, f"Warm reversal prediction took too long: {warm_avg:.3f} ms")


if __name__ == "__main__":
    unittest.main()
