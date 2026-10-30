"""
Unit Test: Notification Account-Wise Isolation & Delete Verification
Verifies:
1. User 1 cannot see User 2's notifications.
2. User 2 cannot see User 1's notifications.
3. Deleting a notification by owner succeeds.
4. User 1 cannot delete User 2's notification (HTTP 403).
5. Clearing all notifications only clears current user's notifications.
"""

import os
import sys
import unittest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.models import Base, User, Notification
from app.api.notifications import (
    list_notifications,
    delete_notification,
    clear_all_notifications,
)
import tempfile


class TestNotificationUserIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.temp_dir.name, "test_notif_iso.db")
        cls.engine = create_engine(f"sqlite:///{cls.db_path}")
        cls.Session = sessionmaker(bind=cls.engine)
        Base.metadata.create_all(bind=cls.engine)

        with cls.Session() as db:
            cls.user1 = User(id=101, name="User One", username="user1", email="user1@test.com", is_verified=True)
            cls.user2 = User(id=102, name="User Two", username="user2", email="user2@test.com", is_verified=True)
            db.add_all([cls.user1, cls.user2])
            db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        try:
            cls.temp_dir.cleanup()
        except Exception:
            pass

    def setUp(self):
        self.db = self.Session()
        self.user1 = self.db.query(User).filter(User.id == 101).first()
        self.user2 = self.db.query(User).filter(User.id == 102).first()
        # Seed test notifications for user1 and user2
        n1 = Notification(id=1, user_id=101, symbol="TCS", event_type="ALERT", priority="INFO", title="User 1 Alert", message="Msg 1")
        n2 = Notification(id=2, user_id=102, symbol="INFY", event_type="ALERT", priority="WARNING", title="User 2 Alert", message="Msg 2")
        self.db.add_all([n1, n2])
        self.db.commit()

    def tearDown(self):
        self.db.query(Notification).delete()
        self.db.commit()
        self.db.close()

    def test_01_user1_cannot_see_user2_notifications(self):
        """User 1 must only see notifications with user_id=101."""
        res = list_notifications(current_user=self.user1, db=self.db)
        self.assertEqual(res["total"], 1)
        self.assertEqual(res["notifications"][0].id, 1)
        self.assertEqual(res["notifications"][0].symbol, "TCS")

    def test_02_user2_cannot_see_user1_notifications(self):
        """User 2 must only see notifications with user_id=102."""
        res = list_notifications(current_user=self.user2, db=self.db)
        self.assertEqual(res["total"], 1)
        self.assertEqual(res["notifications"][0].id, 2)
        self.assertEqual(res["notifications"][0].symbol, "INFY")

    def test_03_delete_single_notification_succeeds_for_owner(self):
        """User 1 can delete their own notification."""
        del_res = delete_notification(1, current_user=self.user1, db=self.db)
        self.assertIn("deleted successfully", del_res["message"])
        # Verify it is gone from DB
        remaining = self.db.query(Notification).filter(Notification.id == 1).first()
        self.assertIsNone(remaining)
        # User 2 notification is untouched
        u2_notif = self.db.query(Notification).filter(Notification.id == 2).first()
        self.assertIsNotNone(u2_notif)

    def test_04_user1_cannot_delete_user2_notification(self):
        """User 1 attempting to delete User 2's notification must be rejected with HTTP 403."""
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            delete_notification(2, current_user=self.user1, db=self.db)
        self.assertEqual(ctx.exception.status_code, 403)
        # User 2's notification still exists
        u2_notif = self.db.query(Notification).filter(Notification.id == 2).first()
        self.assertIsNotNone(u2_notif)

    def test_05_clear_all_only_wipes_current_user_notifications(self):
        """User 1 clear_all wipes only User 1's notifications, leaving User 2 intact."""
        clear_res = clear_all_notifications(current_user=self.user1, db=self.db)
        self.assertEqual(clear_res["deleted"], 1)

        # User 1 has 0
        u1_res = list_notifications(current_user=self.user1, db=self.db)
        self.assertEqual(u1_res["total"], 0)

        # User 2 still has 1
        u2_res = list_notifications(current_user=self.user2, db=self.db)
        self.assertEqual(u2_res["total"], 1)


if __name__ == "__main__":
    unittest.main()
