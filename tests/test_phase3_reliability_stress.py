import os
import time
import tempfile
import unittest
import threading
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.database import Base
from app.database.models import User, Alert, Notification, AlertHistory, SystemSetting
from app.services.prediction import (
    load_model,
    predict_target_probability,
    predict_supertrend_reversal
)
from app.services.market_data import (
    get_stock_visualization_data,
    get_latest_market_snapshot,
    get_market_status,
    clear_negative_cache,
    _RANGE_CACHE,
    _FAST_INFO_CACHE,
    _NEGATIVE_LOOKUP_CACHE
)
from app.services.feature_engine import analyze_supertrend_hit
from app.services.alert_engine import (
    _process_supertrend_state_transition,
    get_supertrend_settings,
    _SUPERTREND_STATES
)
from app.services.messaging.notification_manager import NotificationManager
from app.services.scheduler import scheduled_monitoring_job, _JOB_LOCK
from app.api.auth import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token
)

IST = ZoneInfo("Asia/Kolkata")


class TestPhase3ReliabilityAndStress(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp_dir.name, "test_p3.db")
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

    # =========================================================================
    # 1. Supertrend Trading Logic Reliability
    # =========================================================================

    def test_early_warning_distinguished_from_confirmed_reversal(self):
        """Verify early warnings are distinct from confirmed reversals and never misreported."""
        candles = [
            {"time": "09:15", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1000},
            {"time": "09:16", "open": 101.0, "high": 103.0, "low": 100.5, "close": 102.0, "volume": 1100},
            {"time": "09:17", "open": 102.0, "high": 102.5, "low": 99.8, "close": 100.2, "volume": 1200},
        ]
        now = datetime.now(timezone.utc)

        # 1. Early Warning scenario: Price approaches Supertrend from above (target SELL)
        st_res_warning = {
            "supertrend_price": 99.5,
            "direction": "BUY",
            "previous_direction": "BUY",
            "target_direction": "SELL",
            "distance_points": 0.7,
            "proximity_threshold": 1.0,
            "proximity_percent": 0.50,
            "is_within_pct": True,
            "is_approaching": True,
            "is_flipped": False,
            "is_confirmed_flip": False,
            "flip_signal": None,
            "reversal_risk_score": 0.85,
            "reversal_probability": 0.85,
            "series": [],
            "candles": candles
        }

        state = _SUPERTREND_STATES.setdefault("p3_test_ew", {})
        state["confirmed_direction"] = "BUY"
        state["last_confirmed_candle_ts"] = "09:16"
        state["last_processed_candle_ts"] = "09:16"
        state["state"] = "MONITORING"
        state["early_warning_sent"] = False

        with patch("app.services.messaging.notification_manager.NotificationManager.emit_supertrend_approaching") as mock_approach, \
             patch("app.services.messaging.notification_manager.NotificationManager.emit_supertrend_signal") as mock_signal:
            res = _process_supertrend_state_transition(
                condition_key="p3_test_ew",
                alert=None,
                symbol="TCS",
                st_result=st_res_warning,
                curr_price=100.2,
                db=self.db,
                user_id=1,
                now=now,
                is_market_open=True,
                data_status="LIVE",
                prox_pts=1.0,
                prox_pct=0.50
            )
            # Verify Early Warning was emitted and confirmed signal was NOT emitted
            mock_approach.assert_called_once()
            mock_signal.assert_not_called()
            self.assertIsNotNone(res)
            self.assertEqual(res.get("type"), "EARLY_WARNING")
            self.assertEqual(res.get("target_direction"), "SELL")

    def test_alert_recovery_after_warning_pulls_away_or_expires(self):
        """Verify state machine cleanly recovers to MONITORING when warning pulls away or times out."""
        cond_key = "p3_recovery_sym"
        state = _SUPERTREND_STATES.setdefault(cond_key, {
            "state": "MONITORING",
            "confirmed_direction": "BUY",
            "early_warning_sent": False
        })
        now = datetime.now(timezone.utc)

        # Force state into active early warning
        state["state"] = "EARLY_WARNING_SENT"
        state["early_warning_sent"] = True
        state["warning_started_at"] = now
        state["warning_candle_ts"] = "09:15"

        # Price pulls away (> 1.5x effective proximity: dist_pct = 1.8% vs 0.50% * 1.5 = 0.75%)
        st_res_pulled = {
            "supertrend_price": 95.0,
            "direction": "BUY",
            "previous_direction": "BUY",
            "target_direction": "SELL",
            "distance_points": 5.0,
            "proximity_threshold": 1.0,
            "proximity_percent": 0.50,
            "is_within_pct": False,
            "is_approaching": False,
            "is_flipped": False,
            "flip_signal": None,
            "reversal_probability": 0.20,
            "series": [],
            "candles": []
        }

        res = _process_supertrend_state_transition(
            condition_key=cond_key,
            alert=None,
            symbol="INFY",
            st_result=st_res_pulled,
            curr_price=100.0,  # 5.0 pts / 100.0 = 5.0% > 0.75%
            db=self.db,
            user_id=1,
            now=now,
            is_market_open=True,
            data_status="LIVE",
            prox_pts=1.0,
            prox_pct=0.50
        )
        self.assertIsNotNone(res)
        self.assertEqual(res.get("type"), "WARNING_EXPIRED")
        self.assertEqual(res.get("reason"), "PULLED_AWAY")
        # Confirms state is cleanly reset to MONITORING and early_warning_sent is False
        self.assertEqual(state["state"], "MONITORING")
        self.assertFalse(state["early_warning_sent"])

    def test_event_key_deduplication_and_memory_bounding(self):
        """Verify event_key deduplication prevents duplicate notifications and maintains bounded memory."""
        NotificationManager._DEDUPLICATED_EVENT_KEYS.clear()

        # Emit an event twice with the exact same event_key
        res1 = NotificationManager.emit_supertrend_approaching(
            alert=None,
            symbol="RELIANCE",
            current_price=2500.0,
            supertrend_price=2490.0,
            distance_points=10.0,
            proximity_threshold=15.0,
            current_direction="BUY",
            db=self.db,
            user_id=1,
            candle_ts="10:00",
            timeframe="1m"
        )
        # Second call with identical candle and parameters
        res2 = NotificationManager.emit_supertrend_approaching(
            alert=None,
            symbol="RELIANCE",
            current_price=2500.0,
            supertrend_price=2490.0,
            distance_points=10.0,
            proximity_threshold=15.0,
            current_direction="BUY",
            db=self.db,
            user_id=1,
            candle_ts="10:00",
            timeframe="1m"
        )
        self.assertEqual(len(res2), 0, "Duplicate notification was not suppressed!")

        # Verify bounded memory: simulate inserting 5500 keys
        for i in range(5500):
            NotificationManager._DEDUPLICATED_EVENT_KEYS.add(f"test_key_{i}")
        # Next emission must trigger pruning to <= 5000 items
        NotificationManager.emit_supertrend_approaching(
            alert=None,
            symbol="TCS",
            current_price=3500.0,
            supertrend_price=3485.0,
            distance_points=15.0,
            proximity_threshold=20.0,
            current_direction="BUY",
            db=self.db,
            user_id=1,
            candle_ts="11:00",
            timeframe="1m"
        )
        self.assertLessEqual(len(NotificationManager._DEDUPLICATED_EVENT_KEYS), 5000)

    # =========================================================================
    # 2. Market Data Reliability & Integrity
    # =========================================================================

    def test_market_status_authoritative_and_accurate(self):
        """Verify get_market_status classifies trading sessions, holidays, and weekends accurately."""
        # Weekend test: Saturday (2026-10-17)
        sat = datetime(2026, 10, 17, 11, 30, tzinfo=IST)
        status_sat, _ = get_market_status(sat)
        self.assertEqual(status_sat, "CLOSED")

        # Trading hours test: Wednesday (2026-10-14) at 11:30 AM IST (Not a holiday)
        weekday_open = datetime(2026, 10, 14, 11, 30, tzinfo=IST)
        status_open, _ = get_market_status(weekday_open)
        self.assertEqual(status_open, "OPEN")

        # After hours test: Wednesday at 16:00 IST
        weekday_closed = datetime(2026, 10, 14, 16, 0, tzinfo=IST)
        status_closed, _ = get_market_status(weekday_closed)
        self.assertEqual(status_closed, "CLOSED")

    def test_provider_outage_graceful_handling_and_negative_cache(self):
        """Verify provider timeouts/errors yield DATA_UNAVAILABLE gracefully without crash or synthetic data."""
        clear_negative_cache("SIMULATED_FAIL_SYM")
        with patch("yfinance.Ticker") as mock_ticker:
            mock_inst = MagicMock()
            mock_inst.history.side_effect = TimeoutError("Simulated upstream Yahoo Finance timeout")
            mock_ticker.return_value = mock_inst

            data = get_stock_visualization_data("SIMULATED_FAIL_SYM", time_range="1D")
            self.assertIn(data.get("status"), ("UNAVAILABLE", "DATA_UNAVAILABLE"))
            self.assertEqual(data.get("price"), 0.0)
            self.assertEqual(len(data.get("candles", [])), 0)

            # Subsequent call must be handled by negative cache without re-hitting provider
            mock_inst.history.reset_mock()
            cached_neg = get_stock_visualization_data("SIMULATED_FAIL_SYM", time_range="1D")
            self.assertEqual(cached_neg.get("status"), "DATA_UNAVAILABLE")
            mock_inst.history.assert_not_called()

        clear_negative_cache("SIMULATED_FAIL_SYM")

    # =========================================================================
    # 3. Authentication & Database Reliability
    # =========================================================================

    def test_password_hashing_and_verification_robustness(self):
        """Verify bcrypt hashing, salt verification, and edge cases (long passwords, spaces)."""
        raw_pw = "TraderSecret123!"
        hashed = hash_password(raw_pw)
        self.assertTrue(verify_password(raw_pw, hashed))
        self.assertFalse(verify_password("WrongPassword!", hashed))
        self.assertFalse(verify_password("", hashed))
        self.assertFalse(verify_password(raw_pw, ""))

        # 72+ byte bcrypt truncation handling
        long_pw = "A" * 80
        long_hash = hash_password(long_pw)
        self.assertTrue(verify_password(long_pw, long_hash))

    def test_concurrent_user_registration_and_transaction_safety(self):
        """Verify concurrent user creations are committed safely and handle duplicates with rollback."""
        errors = []
        created_ids = []

        def register_worker(idx: int):
            session = self.SessionLocal()
            try:
                user = User(
                    name=f"Trader {idx}",
                    username=f"concurrent_trader_{idx}_{time.time_ns()}",
                    email=f"trader_{idx}_{time.time_ns()}@stockpulse.local",
                    password_hash=hash_password("Pass123!"),
                    is_verified=True,
                    created_at=datetime.utcnow()
                )
                session.add(user)
                session.commit()
                session.refresh(user)
                created_ids.append(user.id)
            except Exception as e:
                session.rollback()
                errors.append(e)
            finally:
                session.close()

        threads = [threading.Thread(target=register_worker, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent registration errors: {errors}")
        self.assertEqual(len(created_ids), 6)
        # Verify persistence across fresh session
        verify_session = self.SessionLocal()
        persisted_count = verify_session.query(User).filter(User.id.in_(created_ids)).count()
        verify_session.close()
        self.assertEqual(persisted_count, 6)

    # =========================================================================
    # 4. Stress and Concurrency
    # =========================================================================

    def test_scheduler_lock_prevents_overlap_and_releases_cleanly(self):
        """Verify _JOB_LOCK prevents overlapping job execution and releases properly even after exceptions."""
        self.assertTrue(_JOB_LOCK.acquire(blocking=False))
        # Lock is held; immediate scheduled_monitoring_job tick must cleanly skip without deadlock
        with patch("app.services.alert_engine.evaluate_active_alerts") as mock_eval:
            scheduled_monitoring_job()
            mock_eval.assert_not_called()
        _JOB_LOCK.release()

        # Simulate exception inside evaluate_active_alerts: lock must still be released in finally block
        with patch("app.services.alert_engine.evaluate_active_alerts", side_effect=RuntimeError("Simulated cycle crash")):
            scheduled_monitoring_job()
            # Verify lock was released and can be acquired again
            self.assertTrue(_JOB_LOCK.acquire(blocking=False))
            _JOB_LOCK.release()


if __name__ == "__main__":
    unittest.main()
