import unittest
from datetime import datetime
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.models import Base, Alert, Notification, User
from app.database.database import run_db_migrations
from app.services.alert_engine import (
    _process_supertrend_state_transition,
    _SUPERTREND_STATES,
    _STOCK_CONFIRMED_DIRECTIONS
)
import app.services.alert_engine as ae
from app.services.messaging.notification_manager import NotificationManager
from app.services.messaging.notification_service import NotificationService


class TestSuperTrendMultiUserNotifications(unittest.TestCase):
    """
    Test multi-user isolation and notification delivery for SuperTrend flips:
    - When a stock reverses (e.g., BUY to SELL), EVERY user monitoring that stock
      must receive the confirmed hit notification (SUPERTREND_SELL).
    - One user's state transition MUST NOT prematurely mutate another user's state.
    - Multiple monitoring conditions for the SAME user (e.g. alert + watchlist)
      remain synchronized to prevent duplicate spam for the same user.
    """

    def setUp(self):
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

        _SUPERTREND_STATES.clear()
        _STOCK_CONFIRMED_DIRECTIONS.clear()
        NotificationManager._DEDUPLICATED_EVENT_KEYS.clear()

        # Seed two distinct users
        self.user1 = User(id=1, username="user1", email="user1@example.com", password_hash="dummy")
        self.user2 = User(id=2, username="user2", email="user2@example.com", password_hash="dummy")
        self.db.add(self.user1)
        self.db.add(self.user2)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        _SUPERTREND_STATES.clear()
        _STOCK_CONFIRMED_DIRECTIONS.clear()
        NotificationManager._DEDUPLICATED_EVENT_KEYS.clear()

    def test_both_users_receive_confirmed_supertrend_flip_notification(self):
        """
        Verify that when TCS flips from BUY to SELL, User 1 AND User 2
        both receive the confirmed SUPERTREND_SELL notification.
        """
        mock_notif = MagicMock()
        orig_nm = ae.notification_manager
        ae.notification_manager = mock_notif

        try:
            now = datetime.utcnow()
            candle_1000 = "2026-11-02 10:00:00"
            candle_1001 = "2026-11-02 10:01:00"

            # 1. Boot / initialize User 1 and User 2 monitoring TCS in BUY direction
            init_res = {
                "direction": "BUY",
                "supertrend_price": 3800.0,
                "distance_points": 50.0,
                "target_direction": "SELL",
                "is_confirmed_flip": False,
                "is_intrabar_cross": False,
                "is_approaching": False,
                "candle_timestamp": candle_1000,
                "reversal_risk_score": 0.3
            }

            _process_supertrend_state_transition(
                "watchlist_1_TCS", None, "TCS", init_res, 3850.0, self.db, 1, now, True, "LIVE", 1.0
            )
            _process_supertrend_state_transition(
                "watchlist_2_TCS", None, "TCS", init_res, 3850.0, self.db, 2, now, True, "LIVE", 1.0
            )

            self.assertEqual(_SUPERTREND_STATES["watchlist_1_TCS"]["confirmed_direction"], "BUY")
            self.assertEqual(_SUPERTREND_STATES["watchlist_2_TCS"]["confirmed_direction"], "BUY")
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 0)

            # 2. TCS candle completes with confirmed flip to SELL
            flip_res = {
                "direction": "SELL",
                "supertrend_price": 3860.0,
                "distance_points": 20.0,
                "target_direction": "BUY",
                "is_confirmed_flip": True,
                "is_intrabar_cross": False,
                "is_approaching": False,
                "candle_timestamp": candle_1001,
                "reversal_risk_score": 0.85
            }

            # User 1 transition runs first (as in evaluate_active_alerts loop)
            res_user1 = _process_supertrend_state_transition(
                "watchlist_1_TCS", None, "TCS", flip_res, 3840.0, self.db, 1, now, True, "LIVE", 1.0
            )

            self.assertIsNotNone(res_user1)
            self.assertEqual(res_user1["type"], "CONFIRMED_SIGNAL")
            self.assertEqual(res_user1["signal"], "SELL")
            self.assertEqual(_SUPERTREND_STATES["watchlist_1_TCS"]["confirmed_direction"], "SELL")

            # CRITICAL ASSERTION: User 2's state MUST NOT have been polluted by User 1!
            self.assertEqual(
                _SUPERTREND_STATES["watchlist_2_TCS"]["confirmed_direction"],
                "BUY",
                "User 2 confirmed_direction was prematurely overwritten by User 1's transition!"
            )

            # Now User 2 transition runs
            res_user2 = _process_supertrend_state_transition(
                "watchlist_2_TCS", None, "TCS", flip_res, 3840.0, self.db, 2, now, True, "LIVE", 1.0
            )

            self.assertIsNotNone(res_user2)
            self.assertEqual(res_user2["type"], "CONFIRMED_SIGNAL")
            self.assertEqual(res_user2["signal"], "SELL")
            self.assertEqual(_SUPERTREND_STATES["watchlist_2_TCS"]["confirmed_direction"], "SELL")

            # Verify emit_supertrend_signal was called TWICE: once for User 1, once for User 2
            self.assertEqual(mock_notif.emit_supertrend_signal.call_count, 2)

            user_ids_notified = [call.kwargs.get("user_id") for call in mock_notif.emit_supertrend_signal.call_args_list]
            self.assertIn(1, user_ids_notified)
            self.assertIn(2, user_ids_notified)

        finally:
            ae.notification_manager = orig_nm

    def test_same_user_conditions_are_synchronized_without_affecting_other_users(self):
        """
        Verify that multiple conditions for the SAME user (alert + watchlist)
        are synchronized to prevent duplicate spam, while other users remain unaffected.
        """
        now = datetime.utcnow()
        candle_1000 = "2026-11-02 10:00:00"
        candle_1001 = "2026-11-02 10:01:00"

        init_res = {
            "direction": "BUY",
            "supertrend_price": 3800.0,
            "distance_points": 50.0,
            "target_direction": "SELL",
            "is_confirmed_flip": False,
            "candle_timestamp": candle_1000,
            "reversal_risk_score": 0.3
        }

        # User 1 has alert_101 AND watchlist_1_TCS
        _process_supertrend_state_transition("alert_101", None, "TCS", init_res, 3850.0, self.db, 1, now, True, "LIVE", 1.0)
        _process_supertrend_state_transition("watchlist_1_TCS", None, "TCS", init_res, 3850.0, self.db, 1, now, True, "LIVE", 1.0)

        # User 2 has watchlist_2_TCS
        _process_supertrend_state_transition("watchlist_2_TCS", None, "TCS", init_res, 3850.0, self.db, 2, now, True, "LIVE", 1.0)

        flip_res = {
            "direction": "SELL",
            "supertrend_price": 3860.0,
            "distance_points": 20.0,
            "target_direction": "BUY",
            "is_confirmed_flip": True,
            "candle_timestamp": candle_1001,
            "reversal_risk_score": 0.85
        }

        # User 1's alert_101 triggers flip
        _process_supertrend_state_transition("alert_101", None, "TCS", flip_res, 3840.0, self.db, 1, now, True, "LIVE", 1.0)

        # User 1's watchlist_1_TCS must be synchronized to SELL
        self.assertEqual(_SUPERTREND_STATES["watchlist_1_TCS"]["confirmed_direction"], "SELL")

        # But User 2's watchlist_2_TCS must NOT be modified (still BUY)
        self.assertEqual(_SUPERTREND_STATES["watchlist_2_TCS"]["confirmed_direction"], "BUY")


if __name__ == "__main__":
    unittest.main()

