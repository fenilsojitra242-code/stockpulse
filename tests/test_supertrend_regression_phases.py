import unittest
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.models import Base, Alert, Notification, User
from app.database.database import run_db_migrations
from app.services.feature_engine import (
    analyze_supertrend_hit,
    is_candle_complete,
    compute_supertrend,
    sanitize_candles
)
from app.services.alert_engine import (
    _process_supertrend_state_transition,
    _SUPERTREND_STATES,
    _STOCK_CONFIRMED_DIRECTIONS
)
from app.services.messaging.notification_manager import NotificationManager
from app.services.messaging.notification_service import NotificationService
from app.services.messaging.events import NotificationEventType
from app.api.stocks import test_supertrend_notification


class TestSuperTrendRegressionPhases(unittest.TestCase):
    """
    Focused regression test suite covering all Phase 8 requirements:
    1. Intrabar price movement without confirmed direction changes.
    2. Correct completion detection when latest candle is complete or developing.
    3. Repeated polling of the same candle.
    4. Exactly one genuine completed-candle reversal in both BUY-to-SELL and SELL-to-BUY.
    5. Early-warning success, timeout, and pull-away expiration.
    6. Repeated polling after success.
    7. Two monitoring paths observing the same stock.
    8. Stale, missing, delayed, and closed-market data.
    9. Event-key deduplication after restarting the notification service.
    10. Concurrent attempts to persist the same event.
    11. Migration of an existing database with legacy notifications.
    12. Test notification isolation from production alerts.
    """

    def setUp(self):
        # Create an in-memory SQLite database for test isolation with StaticPool for multi-threading
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=False
        )
        Base.metadata.create_all(self.engine)
        run_db_migrations(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        # Clean state caches
        _SUPERTREND_STATES.clear()
        _STOCK_CONFIRMED_DIRECTIONS.clear()
        NotificationManager._DEDUPLICATED_EVENT_KEYS.clear()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        _SUPERTREND_STATES.clear()
        _STOCK_CONFIRMED_DIRECTIONS.clear()
        NotificationManager._DEDUPLICATED_EVENT_KEYS.clear()

    # 1. Intrabar price movement without confirmed direction changes
    def test_01_intrabar_price_movement_without_confirmed_direction_changes(self):
        candles = []
        p = 100.0
        for i in range(12):
            candles.append({
                "open": p - 0.2, "high": p + 0.8, "low": p - 0.5, "close": p,
                "full_time": f"2026-10-16T10:{i:02d}:00+05:30", "time": f"10:{i:02d}"
            })
            p += 0.5

        original_last_close = candles[-1]["close"]
        # Temporary sharp intrabar plunge below supertrend band (e.g. 85.0)
        res = analyze_supertrend_hit(
            candles_or_df=candles,
            current_price=85.0,
            previous_direction="BUY",
            proximity_points=2.0
        )

        # Confirmed direction MUST remain BUY from completed bars
        self.assertEqual(res["direction"], "BUY", "Confirmed direction must remain BUY during developing bar dip")
        self.assertTrue(res["is_intrabar_cross"], "Intrabar penetration must be detected provisionally")
        self.assertFalse(res["is_confirmed_flip"], "Confirmed flip must remain False until completed bar confirms")
        self.assertEqual(res["provisional_direction"], "SELL", "Provisional direction reflects intrabar breach")

        # Historical candle close must NOT be overwritten
        self.assertEqual(candles[-1]["close"], original_last_close, "Historical candle must not be mutated")

    # 2. Correct completion detection when latest candle is complete or developing
    def test_02_correct_completion_detection_when_candle_complete_or_developing(self):
        IST = timezone(timedelta(hours=5, minutes=30))
        candle_ts = datetime(2026, 10, 16, 10, 0, 0, tzinfo=IST)
        candle = {"full_time": candle_ts.isoformat(), "open": 100, "high": 101, "low": 99, "close": 100.5}

        # 30 seconds into 1m candle -> developing
        now_developing = datetime(2026, 10, 16, 10, 0, 30, tzinfo=IST)
        self.assertFalse(
            is_candle_complete(candle, interval="1m", now=now_developing, is_market_open=True),
            "Candle at 10:00:00 is incomplete at 10:00:30"
        )

        # 65 seconds into 1m candle -> completed
        now_completed = datetime(2026, 10, 16, 10, 1, 5, tzinfo=IST)
        self.assertTrue(
            is_candle_complete(candle, interval="1m", now=now_completed, is_market_open=True),
            "Candle at 10:00:00 is complete at 10:01:05"
        )

        # Market closed -> all past candles are finalized
        self.assertTrue(
            is_candle_complete(candle, interval="1m", now=now_developing, is_market_open=False),
            "Market closed means candles are finalized"
        )

    # 3. Repeated polling of the same candle
    def test_03_repeated_polling_of_same_candle(self):
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            st_res = {
                "direction": "BUY",
                "supertrend_price": 95.0,
                "distance_points": 0.8,
                "target_direction": "SELL",
                "is_confirmed_flip": False,
                "is_intrabar_cross": False,
                "is_approaching": True,
                "candle_timestamp": "2026-10-16T10:05:00",
                "reversal_risk_score": 0.75
            }
            now = datetime.utcnow()

            # First poll triggers early warning
            r1 = _process_supertrend_state_transition(
                condition_key="test_poll_rep",
                alert=None,
                symbol="TCS",
                st_result=st_res,
                curr_price=95.8,
                db=self.db,
                user_id=1,
                now=now,
                is_market_open=True,
                data_status="LIVE",
                prox_pts=1.0
            )
            # Boot init
            self.assertIsNone(r1)

            # Second poll triggers early warning
            r2 = _process_supertrend_state_transition(
                condition_key="test_poll_rep",
                alert=None,
                symbol="TCS",
                st_result=st_res,
                curr_price=95.8,
                db=self.db,
                user_id=1,
                now=now,
                is_market_open=True,
                data_status="LIVE",
                prox_pts=1.0
            )
            self.assertIsNotNone(r2)
            self.assertEqual(r2["type"], "EARLY_WARNING")
            self.assertEqual(mock_notif.emit_supertrend_approaching.call_count, 1)

            # Subsequent repeated polls on the SAME candle must NOT duplicate notification
            for _ in range(5):
                r_repeat = _process_supertrend_state_transition(
                    condition_key="test_poll_rep",
                    alert=None,
                    symbol="TCS",
                    st_result=st_res,
                    curr_price=95.8,
                    db=self.db,
                    user_id=1,
                    now=now,
                    is_market_open=True,
                    data_status="LIVE",
                    prox_pts=1.0
                )
                self.assertIsNone(r_repeat)

            self.assertEqual(mock_notif.emit_supertrend_approaching.call_count, 1)
        finally:
            ae.notification_manager = orig_nm

    # 4. Exactly one genuine completed-candle reversal in both BUY-to-SELL and SELL-to-BUY directions
    def test_04_exactly_one_genuine_completed_candle_reversal_both_directions(self):
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            # 1. Start in BUY
            _SUPERTREND_STATES["dir_test"] = {
                "state": "MONITORING",
                "confirmed_direction": "BUY",
                "last_confirmed_candle_ts": "10:00",
                "last_processed_candle_ts": "10:00",
                "last_st_price": 95.0,
                "warning_started_at": None,
                "warning_candle_ts": None,
                "early_warning_sent": False,
                "prediction_cycle_id": None,
                "target_reversal_direction": None,
                "last_dispatched_event_id": None
            }

            # 2. Genuine completed candle flips BUY -> SELL
            sell_res = {
                "direction": "SELL",
                "supertrend_price": 105.0,
                "distance_points": 5.0,
                "target_direction": "BUY",
                "is_confirmed_flip": True,
                "is_intrabar_cross": False,
                "is_approaching": False,
                "candle_timestamp": "10:01",
                "reversal_risk_score": 0.8
            }
            res_sell = _process_supertrend_state_transition(
                condition_key="dir_test", alert=None, symbol="INFY",
                st_result=sell_res, curr_price=100.0, db=self.db, user_id=1,
                now=datetime.utcnow(), is_market_open=True, data_status="LIVE", prox_pts=1.0
            )
            self.assertIsNotNone(res_sell)
            self.assertEqual(res_sell["signal"], "SELL")
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 1)

            # Polling 10:01 again does not duplicate
            res_sell_dup = _process_supertrend_state_transition(
                condition_key="dir_test", alert=None, symbol="INFY",
                st_result=sell_res, curr_price=100.0, db=self.db, user_id=1,
                now=datetime.utcnow(), is_market_open=True, data_status="LIVE", prox_pts=1.0
            )
            self.assertIsNone(res_sell_dup)
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 1)

            # 3. Genuine completed candle flips SELL -> BUY
            buy_res = {
                "direction": "BUY",
                "supertrend_price": 95.0,
                "distance_points": 5.0,
                "target_direction": "SELL",
                "is_confirmed_flip": True,
                "is_intrabar_cross": False,
                "is_approaching": False,
                "candle_timestamp": "10:02",
                "reversal_risk_score": 0.8
            }
            res_buy = _process_supertrend_state_transition(
                condition_key="dir_test", alert=None, symbol="INFY",
                st_result=buy_res, curr_price=100.0, db=self.db, user_id=1,
                now=datetime.utcnow(), is_market_open=True, data_status="LIVE", prox_pts=1.0
            )
            self.assertIsNotNone(res_buy)
            self.assertEqual(res_buy["signal"], "BUY")
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 2)
        finally:
            ae.notification_manager = orig_nm

    # 5. Early-warning success, timeout, and pull-away expiration
    def test_05_early_warning_success_timeout_and_pull_away_expiration(self):
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            # Case A: Pull-away expiration
            _SUPERTREND_STATES["cycle_test"] = {
                "state": "MONITORING",
                "confirmed_direction": "BUY",
                "last_confirmed_candle_ts": "10:00",
                "last_processed_candle_ts": "10:00",
                "last_st_price": 95.0,
                "warning_started_at": None,
                "warning_candle_ts": None,
                "early_warning_sent": False,
                "prediction_cycle_id": None,
                "target_reversal_direction": None,
                "last_dispatched_event_id": None
            }

            # Approach
            app_res = {
                "direction": "BUY", "supertrend_price": 95.0, "distance_points": 0.8,
                "target_direction": "SELL", "is_confirmed_flip": False, "is_intrabar_cross": False,
                "is_approaching": True, "candle_timestamp": "10:01", "reversal_risk_score": 0.8
            }
            t0 = datetime(2026, 10, 16, 10, 1, 0)
            r_warn = _process_supertrend_state_transition(
                "cycle_test", None, "SBIN", app_res, 95.8, self.db, 1, t0, True, "LIVE", 1.0
            )
            self.assertEqual(r_warn["type"], "EARLY_WARNING")
            self.assertIn("cycle_id", r_warn)

            # Price pulls away (distance 3.0 > 1.0 * 1.5)
            away_res = {
                "direction": "BUY", "supertrend_price": 95.0, "distance_points": 3.0,
                "target_direction": "SELL", "is_confirmed_flip": False, "is_intrabar_cross": False,
                "is_approaching": False, "candle_timestamp": "10:02", "reversal_risk_score": 0.2
            }
            r_away = _process_supertrend_state_transition(
                "cycle_test", None, "SBIN", away_res, 98.0, self.db, 1, t0 + timedelta(seconds=30), True, "LIVE", 1.0
            )
            self.assertEqual(r_away["type"], "WARNING_EXPIRED")
            self.assertEqual(r_away["reason"], "PULLED_AWAY")
            self.assertEqual(_SUPERTREND_STATES["cycle_test"]["state"], "MONITORING")

            # Case B: Timeout expiration (305 seconds elapsed)
            app_res_b = dict(app_res, candle_timestamp="10:03")
            r_warn2 = _process_supertrend_state_transition(
                "cycle_test", None, "SBIN", app_res_b, 95.8, self.db, 1, t0 + timedelta(minutes=2), True, "LIVE", 1.0
            )
            self.assertEqual(r_warn2["type"], "EARLY_WARNING")
            # 305s later with no flip
            r_timeout = _process_supertrend_state_transition(
                "cycle_test", None, "SBIN", app_res_b, 95.8, self.db, 1, t0 + timedelta(minutes=2, seconds=305), True, "LIVE", 1.0
            )
            self.assertEqual(r_timeout["type"], "WARNING_EXPIRED")
            self.assertEqual(r_timeout["reason"], "TIMEOUT")
            self.assertEqual(_SUPERTREND_STATES["cycle_test"]["state"], "MONITORING")

            # Case C: Early warning -> Success confirmed on new candle
            app_res_c = dict(app_res, candle_timestamp="10:10")
            r_warn3 = _process_supertrend_state_transition(
                "cycle_test", None, "SBIN", app_res_c, 95.8, self.db, 1, t0 + timedelta(minutes=10), True, "LIVE", 1.0
            )
            self.assertEqual(r_warn3["type"], "EARLY_WARNING")

            flip_res = {
                "direction": "SELL", "supertrend_price": 97.0, "distance_points": 3.0,
                "target_direction": "BUY", "is_confirmed_flip": True, "is_intrabar_cross": False,
                "is_approaching": False, "candle_timestamp": "10:11", "reversal_risk_score": 0.9
            }
            r_success = _process_supertrend_state_transition(
                "cycle_test", None, "SBIN", flip_res, 94.0, self.db, 1, t0 + timedelta(minutes=11), True, "LIVE", 1.0
            )
            self.assertEqual(r_success["type"], "CONFIRMED_SIGNAL")
            self.assertEqual(r_success["signal"], "SELL")
            self.assertEqual(_SUPERTREND_STATES["cycle_test"]["confirmed_direction"], "SELL")
        finally:
            ae.notification_manager = orig_nm

    # 6. Repeated polling after success
    def test_06_repeated_polling_after_success(self):
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            _SUPERTREND_STATES["poll_succ"] = {
                "state": "MONITORING",
                "confirmed_direction": "BUY",
                "last_confirmed_candle_ts": "10:00",
                "last_processed_candle_ts": "10:00",
                "last_st_price": 95.0,
                "warning_started_at": None,
                "warning_candle_ts": None,
                "early_warning_sent": False,
                "prediction_cycle_id": None,
                "target_reversal_direction": None,
                "last_dispatched_event_id": None
            }

            flip_res = {
                "direction": "SELL", "supertrend_price": 105.0, "distance_points": 5.0,
                "target_direction": "BUY", "is_confirmed_flip": True, "is_intrabar_cross": False,
                "is_approaching": False, "candle_timestamp": "10:01", "reversal_risk_score": 0.8
            }
            now = datetime.utcnow()
            res = _process_supertrend_state_transition(
                "poll_succ", None, "TATAMOTORS", flip_res, 100.0, self.db, 1, now, True, "LIVE", 1.0
            )
            self.assertIsNotNone(res)
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 1)

            # Poll 10 more times on the same candle after success
            for _ in range(10):
                res_rep = _process_supertrend_state_transition(
                    "poll_succ", None, "TATAMOTORS", flip_res, 100.0, self.db, 1, now, True, "LIVE", 1.0
                )
                self.assertIsNone(res_rep)

            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 1)
        finally:
            ae.notification_manager = orig_nm

    # 7. Two monitoring paths observing the same stock
    def test_07_two_monitoring_paths_observing_same_stock(self):
        # Path 1: Alert-based monitoring condition
        st_res_init = {
            "direction": "BUY", "supertrend_price": 100.0, "distance_points": 5.0,
            "target_direction": "SELL", "is_confirmed_flip": False, "is_intrabar_cross": False,
            "is_approaching": False, "candle_timestamp": "10:00", "reversal_risk_score": 0.5
        }
        now = datetime.utcnow()
        _process_supertrend_state_transition(
            "alert_999", None, "HDFCBANK", st_res_init, 105.0, self.db, 1, now, True, "LIVE", 1.0
        )
        self.assertEqual(_SUPERTREND_STATES["alert_999"]["confirmed_direction"], "BUY")

        # Path 2: Watchlist monitoring boots for same stock -> inherits unified confirmed direction
        _process_supertrend_state_transition(
            "watchlist_1_HDFCBANK", None, "HDFCBANK", st_res_init, 105.0, self.db, 1, now, True, "LIVE", 1.0
        )
        self.assertEqual(_SUPERTREND_STATES["watchlist_1_HDFCBANK"]["confirmed_direction"], "BUY")

        # Now Alert confirms flip to SELL
        st_flip = {
            "direction": "SELL", "supertrend_price": 110.0, "distance_points": 5.0,
            "target_direction": "BUY", "is_confirmed_flip": True, "is_intrabar_cross": False,
            "is_approaching": False, "candle_timestamp": "10:01", "reversal_risk_score": 0.8
        }
        _process_supertrend_state_transition(
            "alert_999", None, "HDFCBANK", st_flip, 105.0, self.db, 1, now, True, "LIVE", 1.0
        )
        self.assertEqual(_SUPERTREND_STATES["alert_999"]["confirmed_direction"], "SELL")

        # Verify Watchlist condition was synchronized and does not emit contradictory signal
        self.assertEqual(_SUPERTREND_STATES["watchlist_1_HDFCBANK"]["confirmed_direction"], "SELL")

    # 8. Stale, missing, delayed, and closed-market data
    def test_08_stale_missing_delayed_and_closed_market_data(self):
        mock_notif = MagicMock()
        import app.services.alert_engine as ae
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            now = datetime.utcnow()
            # A: Closed market -> No transitions emitted
            res_closed = _process_supertrend_state_transition(
                "closed_test", None, "WIPRO",
                {"direction": "SELL", "supertrend_price": 100.0, "distance_points": 1.0,
                 "target_direction": "BUY", "is_confirmed_flip": True, "is_intrabar_cross": False,
                 "is_approaching": True, "candle_timestamp": "16:00"},
                99.0, self.db, 1, now, is_market_open=False, data_status="CLOSED", prox_pts=1.0
            )
            self.assertIsNone(res_closed)
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 0)

            # B: Data unavailable -> state DATA_UNAVAILABLE
            res_unavail = _process_supertrend_state_transition(
                "unavail_test", None, "WIPRO",
                {"direction": "BUY", "supertrend_price": 100.0, "distance_points": 1.0,
                 "target_direction": "SELL", "is_confirmed_flip": False, "is_intrabar_cross": False,
                 "is_approaching": False, "candle_timestamp": "10:00"},
                0.0, self.db, 1, now, is_market_open=True, data_status="DATA_UNAVAILABLE", prox_pts=1.0
            )
            self.assertEqual(res_unavail["status"], "DATA_UNAVAILABLE")

            # C: Stale feed in feature_engine -> no confirmed flips generated
            st_stale = analyze_supertrend_hit(
                candles_or_df=[{"open": 100, "high": 101, "low": 99, "close": 98, "full_time": "10:00"},
                               {"open": 98, "high": 99, "low": 90, "close": 91, "full_time": "10:01"}],
                current_price=91.0,
                previous_direction="BUY",
                data_status="STALE"
            )
            self.assertFalse(st_stale["is_confirmed_flip"], "Stale data must not generate confirmed flip")
            self.assertFalse(st_stale["is_approaching"], "Stale data must not generate early warning")
        finally:
            ae.notification_manager = orig_nm

    # 9. Event-key deduplication after restarting the notification service
    def test_09_event_key_deduplication_after_restarting_notification_service(self):
        svc1 = NotificationService()
        event_key = "st_signal:1:RELIANCE:1m:2026-10-16T10:00:00:BUY->SELL"
        data = {
            "user_id": 1,
            "symbol": "RELIANCE",
            "current_price": 2500.0,
            "supertrend_price": 2510.0,
            "event_key": event_key
        }

        # First dispatch with service instance 1
        res1 = svc1.dispatch(
            event_type=NotificationEventType.SUPERTREND_SELL.value,
            data=data,
            db=self.db
        )
        self.assertTrue(any(r.get("status") == "SENT" for r in res1))

        # Check in database
        saved = self.db.query(Notification).filter(Notification.event_key == event_key).first()
        self.assertIsNotNone(saved)
        self.assertEqual(saved.event_key, event_key)

        # Simulate service restart (new service instance with clean in-memory state)
        svc2 = NotificationService()
        res2 = svc2.dispatch(
            event_type=NotificationEventType.SUPERTREND_SELL.value,
            data=data,
            db=self.db
        )
        # Must be deduplicated via database
        self.assertTrue(any(r.get("duplicate") is True for r in res2))

        # Exactly ONE record exists in the database
        count = self.db.query(Notification).filter(Notification.event_key == event_key).count()
        self.assertEqual(count, 1)

    # 10. Concurrent attempts to persist the same event
    def test_10_concurrent_attempts_to_persist_same_event(self):
        import tempfile
        import os
        tf = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        tf.close()
        db_path = tf.name

        try:
            file_engine = create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 15})
            Base.metadata.create_all(file_engine)
            run_db_migrations(file_engine)
            ThreadSession = sessionmaker(bind=file_engine)

            event_key = "st_signal:1:TCS:1m:2026-10-16T10:00:00:SELL->BUY"
            data = {
                "user_id": 1,
                "symbol": "TCS",
                "current_price": 3800.0,
                "supertrend_price": 3790.0,
                "event_key": event_key
            }

            svc = NotificationService()
            errors = []

            def worker():
                db_thread = ThreadSession()
                try:
                    svc.dispatch(
                        event_type=NotificationEventType.SUPERTREND_BUY.value,
                        data=data,
                        db=db_thread
                    )
                except Exception as e:
                    errors.append(e)
                finally:
                    db_thread.close()

            threads = [threading.Thread(target=worker) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(len(errors), 0, f"Concurrent dispatches raised unexpected errors: {errors}")

            # Unique constraint in database guaranteed exactly one row persisted
            chk_session = ThreadSession()
            try:
                count = chk_session.query(Notification).filter(Notification.event_key == event_key).count()
                self.assertEqual(count, 1, "Exactly one notification must be persisted across concurrent workers")
            finally:
                chk_session.close()
                file_engine.dispose()
        finally:
            try:
                if os.path.exists(db_path):
                    os.remove(db_path)
            except Exception:
                pass

    # 11. Migration of an existing database with legacy notifications
    def test_11_migration_of_existing_database_with_legacy_notifications(self):
        legacy_engine = create_engine("sqlite:///:memory:", echo=False)
        with legacy_engine.connect() as conn:
            # Create legacy notifications table WITHOUT event_key column
            conn.execute(text("""
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    alert_id INTEGER,
                    symbol VARCHAR(30) NOT NULL,
                    event_type VARCHAR(50) NOT NULL,
                    priority VARCHAR(20) DEFAULT 'INFO',
                    title VARCHAR(255) NOT NULL,
                    message TEXT NOT NULL,
                    channel VARCHAR(50) DEFAULT 'popup',
                    status VARCHAR(30) DEFAULT 'SENT',
                    retry_count INTEGER DEFAULT 0,
                    error TEXT,
                    created_at TIMESTAMP,
                    sent_at TIMESTAMP,
                    is_read BOOLEAN DEFAULT 0,
                    extra_data TEXT
                )
            """))
            # Insert legacy notifications
            conn.execute(text("""
                INSERT INTO notifications (symbol, event_type, title, message)
                VALUES ('RELIANCE', 'SUPERTREND_BUY', 'Old Alert', 'Legacy message 1'),
                       ('INFY', 'SUPERTREND_SELL', 'Old Alert 2', 'Legacy message 2')
            """))
            conn.commit()

        # Run migration on legacy database
        run_db_migrations(legacy_engine)

        with legacy_engine.connect() as conn:
            # Verify event_key column exists
            result = conn.execute(text("PRAGMA table_info(notifications)")).fetchall()
            col_names = [row[1] for row in result]
            self.assertIn("event_key", col_names, "Migration must add event_key column")

            # Verify legacy notifications are intact
            rows = conn.execute(text("SELECT id, symbol, event_key FROM notifications")).fetchall()
            self.assertEqual(len(rows), 2)
            for r in rows:
                self.assertIsNone(r[2], "Legacy notifications have event_key = NULL without error")

            # Verify new notification with event_key can be inserted and unique constraint works
            conn.execute(text("INSERT INTO notifications (symbol, event_type, title, message, event_key) VALUES ('TCS', 'BUY', 'T', 'M', 'key1')"))
            conn.commit()

            with self.assertRaises(Exception):
                conn.execute(text("INSERT INTO notifications (symbol, event_type, title, message, event_key) VALUES ('TCS', 'BUY', 'T', 'M', 'key1')"))
                conn.commit()

        legacy_engine.dispose()

    # 12. Test notification isolation from production alerts
    def test_12_test_notification_isolation_from_production_alerts(self):
        user = User(id=1, email="test@stockpulse.com", username="testuser")
        self.db.add(user)
        self.db.commit()

        # Create real production alert in database
        prod_alert = Alert(
            id=777,
            user_id=1,
            symbol="RELIANCE",
            direction="UP",
            target_percent=1.5,
            warning_minutes=5,
            enable_supertrend=True,
            last_supertrend_direction="BUY",
            last_event="NONE",
            supertrend_approaching_sent=False,
            status="ACTIVE"
        )
        self.db.add(prod_alert)
        self.db.commit()

        # Mock market data visualization
        with patch("app.services.market_data.get_stock_visualization_data") as mock_vis:
            mock_vis.return_value = {"price": 2500.0, "symbol": "RELIANCE"}

            # Execute simulation test notification for SELL
            res = test_supertrend_notification(
                symbol="RELIANCE",
                signal_type="SELL",
                current_user=user,
                db=self.db
            )
            self.assertEqual(res["status"], "success")
            self.assertTrue(res["is_simulation"])

        # Reload alert from DB and verify it was NEVER mutated
        self.db.expire_all()
        reloaded_alert = self.db.query(Alert).filter(Alert.id == 777).first()
        self.assertEqual(reloaded_alert.last_supertrend_direction, "BUY", "Production alert direction must NOT change")
        self.assertEqual(reloaded_alert.last_event, "NONE", "Production alert last_event must NOT be overwritten by test")
        self.assertFalse(reloaded_alert.supertrend_approaching_sent)


if __name__ == "__main__":
    unittest.main()
