import unittest
import os
import tempfile
import pandas as pd
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from unittest.mock import patch, MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

from app.database.models import Base, User, Alert, Notification, AlertHistory
from app.services.market_data import (
    get_market_status,
    get_stock_visualization_data,
    IST,
    US_ET,
    _RANGE_CACHE
)
from app.services.alert_engine import (
    _process_supertrend_state_transition,
    _SUPERTREND_STATES
)


class TestAuditBugReproductions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.test_db_path = os.path.join(cls.temp_dir.name, "test_audit.db")
        cls.test_engine = create_engine(
            f"sqlite:///{cls.test_db_path}",
            connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(bind=cls.test_engine)
        cls.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=cls.test_engine)

    @classmethod
    def tearDownClass(cls):
        cls.test_engine.dispose()
        cls.temp_dir.cleanup()

    def setUp(self):
        self.db = self.SessionLocal()
        _RANGE_CACHE.clear()

    def tearDown(self):
        self.db.close()

    def test_history_module_has_http_exception(self):
        """Verify HTTPException is imported in app.api.history so exception handling doesn't crash."""
        import app.api.history as hist_mod
        self.assertTrue(hasattr(hist_mod, "HTTPException"), "app.api.history must import HTTPException from fastapi")

    def test_us_symbol_market_status_in_visualization_during_us_open_hours(self):
        """Verify get_stock_visualization_data uses US market hours for US stocks, not Indian market hours.
        At 20:00 IST on a Tuesday (10:30 AM US ET), Indian market is CLOSED but US market is OPEN.
        """
        simulated_time_ist = datetime(2026, 10, 20, 20, 0, 0, tzinfo=IST)  # Tuesday 8:00 PM IST = 10:30 AM ET

        # Mock yfinance to return a candle timestamped 1 minute ago in US ET
        candle_time_et = datetime(2026, 10, 20, 10, 29, 0, tzinfo=US_ET)
        mock_df = pd.DataFrame(
            [{
                "open": 230.0,
                "high": 232.0,
                "low": 229.5,
                "close": 231.5,
                "volume": 50000
            }],
            index=[candle_time_et]
        )

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = mock_df
        mock_ticker.fast_info = MagicMock(currency="USD", last_price=231.5, previous_close=228.0)

        with patch("app.services.market_data._get_yf") as mock_yf, \
             patch("app.services.market_data.datetime") as mock_dt:
            mock_yf.return_value.Ticker.return_value = mock_ticker
            mock_dt.now.side_effect = lambda tz=None: simulated_time_ist if tz == IST else datetime.now(tz)
            mock_dt.fromisoformat = datetime.fromisoformat

            data = get_stock_visualization_data("AAPL", "1D")
            self.assertEqual(data["market_status"], "OPEN", "US stock AAPL should be OPEN at 10:30 AM ET (20:00 IST)")
            self.assertEqual(data["data_status"], "LIVE", "Fresh candle during US trading hours should be LIVE, not CLOSED")

    def test_no_fabricated_52w_high_low(self):
        """Verify 52w high and low are never fabricated as curr_price * 1.25 or 0.75."""
        simulated_time_ist = datetime(2026, 10, 20, 11, 0, 0, tzinfo=IST)
        mock_df = pd.DataFrame(
            [{
                "open": 100.0,
                "high": 105.0,
                "low": 98.0,
                "close": 102.0,
                "volume": 1000
            }],
            index=[datetime(2026, 10, 20, 10, 59, 0, tzinfo=IST)]
        )

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = mock_df
        # fast_info without 52-week stats
        mock_ticker.fast_info = MagicMock(currency="INR", last_price=102.0, year_high=None, year_low=None, previous_close=100.0)

        with patch("app.services.market_data._get_yf") as mock_yf, \
             patch("app.services.market_data.datetime") as mock_dt:
            mock_yf.return_value.Ticker.return_value = mock_ticker
            mock_dt.now.side_effect = lambda tz=None: simulated_time_ist if tz == IST else datetime.now(tz)
            mock_dt.fromisoformat = datetime.fromisoformat

            # Test an unlisted / custom symbol not in STOCK_METADATA
            data = get_stock_visualization_data("CUSTOMSTOCK", "1D")
            # Should NOT be fabricated as 102.0 * 1.25 = 127.5 or 102.0 * 0.75 = 76.5
            self.assertNotEqual(data["fifty_two_week_high"], 127.5, "52w high must not be fabricated as curr_price * 1.25")
            self.assertNotEqual(data["fifty_two_week_low"], 76.5, "52w low must not be fabricated as curr_price * 0.75")


    def test_supertrend_symbol_exact_synchronization_no_substring_collision(self):
        """Verify state synchronization uses exact symbol matching, not substring containment.
        When LT flips, it must NOT mutate VOLTAS simply because 'LT' is a substring of 'VOLTAS'!
        """
        _SUPERTREND_STATES.clear()

        # Set up a state for VOLTAS (SELL)
        now = datetime.now(timezone.utc)
        st_res_voltas = {
            "direction": "SELL",
            "supertrend_price": 1400.0,
            "distance_points": 10.0,
            "target_direction": "BUY",
            "is_confirmed_flip": False,
            "reversal_confidence_threshold": 0.5
        }
        _process_supertrend_state_transition(
            condition_key="watchlist_all_VOLTAS",
            alert=None,
            symbol="VOLTAS",
            st_result=st_res_voltas,
            curr_price=1390.0,
            db=self.db,
            user_id=1,
            now=now,
            is_market_open=True,
            data_status="LIVE",
            prox_pts=1.0
        )
        self.assertEqual(_SUPERTREND_STATES["watchlist_all_VOLTAS"]["confirmed_direction"], "SELL")

        # Set up initial state for LT (SELL)
        st_res_lt_init = {
            "direction": "SELL",
            "supertrend_price": 3600.0,
            "distance_points": 10.0,
            "target_direction": "BUY",
            "is_confirmed_flip": False,
            "reversal_confidence_threshold": 0.5
        }
        _process_supertrend_state_transition(
            condition_key="watchlist_all_LT",
            alert=None,
            symbol="LT",
            st_result=st_res_lt_init,
            curr_price=3590.0,
            db=self.db,
            user_id=1,
            now=now,
            is_market_open=True,
            data_status="LIVE",
            prox_pts=1.0
        )
        self.assertEqual(_SUPERTREND_STATES["watchlist_all_LT"]["confirmed_direction"], "SELL")

        # Now LT flips to BUY on a completed candle
        st_res_lt_flip = {
            "direction": "BUY",
            "supertrend_price": 3580.0,
            "distance_points": 10.0,
            "target_direction": "SELL",
            "is_confirmed_flip": True,
            "candle_timestamp": "2026-10-23T10:00:00",
            "reversal_confidence_threshold": 0.5
        }
        _process_supertrend_state_transition(
            condition_key="watchlist_all_LT",
            alert=None,
            symbol="LT",
            st_result=st_res_lt_flip,
            curr_price=3610.0,
            db=self.db,
            user_id=1,
            now=now,
            is_market_open=True,
            data_status="LIVE",
            prox_pts=1.0
        )

        # VOLTAS should STILL be SELL and MUST NOT have been mutated to BUY by LT's reversal!
        self.assertEqual(
            _SUPERTREND_STATES["watchlist_all_VOLTAS"]["confirmed_direction"],
            "SELL",
            "VOLTAS confirmed_direction was incorrectly mutated by LT flip due to 'LT' in 'WATCHLIST_ALL_VOLTAS'!"
        )



    def test_alert_isolation_rejects_unowned_or_null_user_alert(self):
        """Verify alert endpoints strictly forbid accessing unowned alerts or alerts with user_id=None."""
        from app.api.alerts import get_alert, delete_alert

        user1 = User(name="User One", email="user1@example.com", password_hash="hash1")
        user2 = User(name="User Two", email="user2@example.com", password_hash="hash2")
        self.db.add_all([user1, user2])
        self.db.commit()
        self.db.refresh(user1)
        self.db.refresh(user2)

        # Alert with user_id=None (orphaned/unassigned)
        alert_orphan = Alert(
            user_id=None,
            symbol="INFY",
            target_percent=2.0,
            warning_minutes=5,
            status="MONITORING"
        )
        # Alert belonging to user1
        alert_user1 = Alert(
            user_id=user1.id,
            symbol="TCS",
            target_percent=2.0,
            warning_minutes=5,
            status="MONITORING"
        )
        self.db.add_all([alert_orphan, alert_user1])
        self.db.commit()
        self.db.refresh(alert_orphan)
        self.db.refresh(alert_user1)

        # User2 tries to get User1's alert -> must raise 403
        with self.assertRaises(HTTPException) as ctx:
            get_alert(alert_user1.id, current_user=user2, db=self.db)
        self.assertEqual(ctx.exception.status_code, 403)

        # User2 tries to get orphan alert (user_id=None) -> must raise 403
        with self.assertRaises(HTTPException) as ctx:
            get_alert(alert_orphan.id, current_user=user2, db=self.db)
        self.assertEqual(ctx.exception.status_code, 403)

        # User2 tries to delete User1's alert -> must raise 403
        with self.assertRaises(HTTPException) as ctx:
            delete_alert(alert_user1.id, current_user=user2, db=self.db)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_clear_notifications_preserves_system_notifications(self):
        """Verify clearing all notifications only removes the current user's notifications, not global system ones."""
        from app.api.notifications import clear_all_notifications

        user = User(name="Clear User", email="clear@example.com", password_hash="hash")
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)

        user_notif = Notification(
            user_id=user.id,
            symbol="TCS",
            event_type="ALERT",
            priority="INFO",
            title="User Alert",
            message="User specific",
            channel="popup"
        )
        sys_notif = Notification(
            user_id=None,
            symbol="SYSTEM",
            event_type="SYSTEM_STATUS",
            priority="INFO",
            title="System Active",
            message="Global health check",
            channel="dashboard"
        )
        self.db.add_all([user_notif, sys_notif])
        self.db.commit()

        # User clears all notifications
        clear_all_notifications(current_user=user, db=self.db)

        # User's notification should be deleted
        remaining_user_notif = self.db.query(Notification).filter(Notification.user_id == user.id).first()
        self.assertIsNone(remaining_user_notif)

        # Global system notification must NOT be deleted!
        remaining_sys_notif = self.db.query(Notification).filter(Notification.user_id.is_(None)).first()
        self.assertIsNotNone(remaining_sys_notif, "System notification was accidentally deleted by user clear-all!")

    def test_52w_high_low_returns_none_on_intraday_data_without_provider_stats(self):
        """Verify 52w statistics return None on intraday 1D/5D data when provider doesn't have 52w stats,
        and never falsely adopt the intraday high/low as the 52w high/low."""
        simulated_time_ist = datetime(2026, 10, 20, 11, 0, 0, tzinfo=IST)
        mock_df = pd.DataFrame(
            [{
                "open": 500.0,
                "high": 520.0,
                "low": 490.0,
                "close": 510.0,
                "volume": 2500
            }],
            index=[datetime(2026, 10, 20, 10, 59, 0, tzinfo=IST)]
        )

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = mock_df
        # No 52-week stats in fast_info
        mock_ticker.fast_info = MagicMock(currency="INR", last_price=510.0, year_high=None, year_low=None, previous_close=500.0)

        with patch("app.services.market_data._get_yf") as mock_yf, \
             patch("app.services.market_data.datetime") as mock_dt:
            mock_yf.return_value.Ticker.return_value = mock_ticker
            mock_dt.now.side_effect = lambda tz=None: simulated_time_ist if tz == IST else datetime.now(tz)
            mock_dt.fromisoformat = datetime.fromisoformat

            data = get_stock_visualization_data("UNKNOWNSTOCK", "1D")
            self.assertIsNone(data["fifty_two_week_high"], "Intraday max must NOT be reported as 52-week high")
            self.assertIsNone(data["fifty_two_week_low"], "Intraday min must NOT be reported as 52-week low")

    def test_market_snapshot_no_fabricated_price_default(self):
        """Verify get_latest_market_snapshot returns 0.0, not 100.0, when market data has missing prices."""
        from app.services.market_data import get_latest_market_snapshot, _SNAPSHOT_CACHE
        _SNAPSHOT_CACHE.clear()

        # Mock get_stock_visualization_data to return empty/unavailable data
        empty_data = {
            "status": "DATA_UNAVAILABLE",
            "symbol": "NODATA",
            "candles": [],
            "price": None,
            "previous_close": None,
            "open": None,
            "day_high": None,
            "day_low": None
        }

        with patch("app.services.market_data.get_stock_visualization_data", return_value=empty_data):
            snap = get_latest_market_snapshot("NODATA")
            self.assertEqual(snap["price"], 0.0, "Missing price must default to 0.0, not 100.0")
            self.assertEqual(snap["open"], 0.0, "Missing open must default to 0.0, not 100.0")
            self.assertEqual(snap["high"], 0.0, "Missing high must default to 0.0, not 100.0")
            self.assertEqual(snap["low"], 0.0, "Missing low must default to 0.0, not 100.0")

    def test_history_clear_handles_database_exception_cleanly(self):
        """Verify clear_history raises clean HTTPException 500 when database commit fails."""
        from app.api.history import clear_history

        mock_db = MagicMock()
        mock_db.commit.side_effect = Exception("DB Disk I/O failure")
        mock_user = MagicMock(id=42)

        with self.assertRaises(HTTPException) as ctx:
            clear_history(current_user=mock_user, db=mock_db)
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("Database transaction failed", ctx.exception.detail)
        mock_db.rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
