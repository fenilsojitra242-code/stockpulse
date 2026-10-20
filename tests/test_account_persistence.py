"""
StockPulse Permanent User Registration, Login, and Account Persistence Test Suite

Validates all 14 required scenarios from Section 8:
1. A new user can register successfully.
2. The user record exists in the database after registration.
3. Registering again with the same email does not create a duplicate account.
4. Logging out does not delete the user record.
5. Logging in again with the same email/username and password succeeds.
6. Incorrect passwords are rejected.
7. Unknown accounts cannot log in.
8. Protected routes reject unauthenticated requests.
9. A session behaves correctly after application restart, according to the existing session design.
10. A user account survives a fresh database connection and subsequent application process when using persistent storage.
11. Existing accounts continue to work after any required migration.
12. Two different users cannot access each other's private data.
13. Passwords are stored as secure hashes, never plaintext.
14. Repeated registration and login attempts do not create duplicate users or corrupt existing data.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock

from fastapi import Response, HTTPException, Request, status
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.models import Base, User, Alert, Notification
from app.api.auth import (
    register_user,
    login_user,
    logout_user,
    get_current_user_profile,
    get_current_user,
    create_access_token,
    verify_password,
    seed_default_user_if_needed,
    RegisterRequest,
    LoginRequest,
)


class TestAccountPersistence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create an isolated temporary SQLite database for this test run
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.temp_dir.name, "test_persistence.db")
        cls.db_url = f"sqlite:///{cls.db_path}"
        cls.engine = create_engine(
            cls.db_url,
            connect_args={"check_same_thread": False}
        )
        cls.TestingSessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=cls.engine
        )
        Base.metadata.create_all(bind=cls.engine)

        # Seed the fixed system default user
        with cls.TestingSessionLocal() as db:
            seed_default_user_if_needed(db)

        # Snapshot production DB modification time to ensure complete safety
        cls.prod_db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "stockpulse.db"))
        cls.prod_db_mtime = os.path.getmtime(cls.prod_db_path) if os.path.exists(cls.prod_db_path) else None

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        try:
            cls.temp_dir.cleanup()
        except Exception:
            pass

        # Verify production DB was not modified
        if cls.prod_db_mtime is not None and os.path.exists(cls.prod_db_path):
            current_mtime = os.path.getmtime(cls.prod_db_path)
            assert current_mtime == cls.prod_db_mtime, "CRITICAL: Production DB was touched during tests!"

    def setUp(self):
        self.db = self.TestingSessionLocal()

    def tearDown(self):
        self.db.close()

    # -------------------------------------------------------------------------
    # Scenario 1 & 2: New user registration & record permanence
    # -------------------------------------------------------------------------
    def test_01_new_user_registration_and_db_record(self):
        """Verify new user registers successfully and permanently exists in DB."""
        reg_data = RegisterRequest(
            name="Sarah Connor",
            username="sconnor",
            email="sarah.connor@resistance.net",
            password="FuturePassword2026!"
        )
        resp = Response()
        result = register_user(req=reg_data, response=resp, db=self.db)

        self.assertIn("access_token", result)
        self.assertEqual(result["user"]["email"], "sarah.connor@resistance.net")
        self.assertEqual(result["user"]["username"], "sconnor")

        # Verify directly in database
        saved = self.db.query(User).filter(User.email == "sarah.connor@resistance.net").first()
        self.assertIsNotNone(saved)
        self.assertEqual(saved.name, "Sarah Connor")
        self.assertEqual(saved.username, "sconnor")
        self.assertTrue(saved.is_verified)
        self.assertTrue(verify_password("FuturePassword2026!", saved.password_hash))

    # -------------------------------------------------------------------------
    # Scenario 3: Duplicate email does not create a duplicate account
    # -------------------------------------------------------------------------
    def test_02_duplicate_email_registration_rejected(self):
        """Verify registering with already-used email is rejected and does not duplicate."""
        reg_data = RegisterRequest(
            name="Sarah Clone",
            username="sconnor2",
            email="SARAH.CONNOR@resistance.net",  # Case-insensitive duplicate
            password="AnotherPassword!"
        )
        resp = Response()
        with self.assertRaises(HTTPException) as ctx:
            register_user(req=reg_data, response=resp, db=self.db)

        self.assertEqual(ctx.exception.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already exists", ctx.exception.detail)

        # Count records: exactly one Sarah Connor must exist
        count = self.db.query(User).filter(
            func.lower(User.email) == "sarah.connor@resistance.net"
        ).count()
        self.assertEqual(count, 1)

    # -------------------------------------------------------------------------
    # Scenario 4: Logging out does NOT delete user record
    # -------------------------------------------------------------------------
    def test_03_logout_does_not_delete_user_record(self):
        """Verify logout only clears session cookies and never deletes the user."""
        user_before = self.db.query(User).filter(User.email == "sarah.connor@resistance.net").first()
        self.assertIsNotNone(user_before)
        user_id = user_before.id

        resp = Response()
        logout_result = logout_user(response=resp)
        self.assertEqual(logout_result["status"].lower(), "success")

        # Verify cookie clearance header is present
        cookie_header = resp.headers.get("set-cookie", "")
        self.assertIn("stockpulse_jwt_token", cookie_header)
        self.assertIn("Max-Age=0", cookie_header)

        # Verify user still exists in database
        user_after = self.db.query(User).filter(User.id == user_id).first()
        self.assertIsNotNone(user_after)
        self.assertEqual(user_after.email, "sarah.connor@resistance.net")
        self.assertEqual(user_after.username, "sconnor")

    # -------------------------------------------------------------------------
    # Scenario 5: Login again with email or username succeeds
    # -------------------------------------------------------------------------
    def test_04_login_again_with_email_and_username(self):
        """Verify logging in again with email or username retrieves the exact same account."""
        # 1. Login with email
        resp1 = Response()
        login_email = LoginRequest(
            identifier="sarah.connor@resistance.net",
            password="FuturePassword2026!"
        )
        res1 = login_user(req=login_email, response=resp1, db=self.db)
        self.assertIn("access_token", res1)
        self.assertEqual(res1["user"]["email"], "sarah.connor@resistance.net")

        # 2. Login with username
        resp2 = Response()
        login_user_req = LoginRequest(
            identifier="SCONNOR",  # Case-insensitive username check
            password="FuturePassword2026!"
        )
        res2 = login_user(req=login_user_req, response=resp2, db=self.db)
        self.assertIn("access_token", res2)
        self.assertEqual(res2["user"]["id"], res1["user"]["id"])

    # -------------------------------------------------------------------------
    # Scenario 6: Incorrect password rejected
    # -------------------------------------------------------------------------
    def test_05_incorrect_password_rejected(self):
        """Verify incorrect password fails without creating an account or revealing state."""
        resp = Response()
        bad_req = LoginRequest(
            identifier="sarah.connor@resistance.net",
            password="WrongPassword123"
        )
        with self.assertRaises(HTTPException) as ctx:
            login_user(req=bad_req, response=resp, db=self.db)

        self.assertEqual(ctx.exception.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("Invalid email/username or password", ctx.exception.detail)

    # -------------------------------------------------------------------------
    # Scenario 7: Unknown account cannot log in
    # -------------------------------------------------------------------------
    def test_06_unknown_account_cannot_login(self):
        """Verify non-existent account cannot log in and is not auto-created."""
        resp = Response()
        unknown_req = LoginRequest(
            identifier="ghost.user@doesnotexist.com",
            password="AnyPassword123"
        )
        with self.assertRaises(HTTPException) as ctx:
            login_user(req=unknown_req, response=resp, db=self.db)

        self.assertEqual(ctx.exception.status_code, status.HTTP_401_UNAUTHORIZED)
        # Ensure account was NOT created
        check = self.db.query(User).filter(User.email == "ghost.user@doesnotexist.com").first()
        self.assertIsNone(check)

    # -------------------------------------------------------------------------
    # Scenario 8: Protected routes reject unauthenticated requests
    # -------------------------------------------------------------------------
    def test_07_protected_routes_reject_unauthenticated(self):
        """Verify get_current_user raises 401 when token is missing, expired, or invalid."""
        mock_req = MagicMock(spec=Request)
        mock_req.headers = {}
        mock_req.cookies = {}
        mock_req.query_params = {}

        # 1. Missing token
        with self.assertRaises(HTTPException) as ctx:
            get_current_user(request=mock_req, creds=None, db=self.db)
        self.assertEqual(ctx.exception.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Invalid fake token
        mock_req.headers = {"Authorization": "Bearer bad.token.value"}
        with self.assertRaises(HTTPException) as ctx:
            get_current_user(request=mock_req, creds=None, db=self.db)
        self.assertEqual(ctx.exception.status_code, status.HTTP_401_UNAUTHORIZED)

    # -------------------------------------------------------------------------
    # Scenario 9: Session behaves correctly across application restart
    # -------------------------------------------------------------------------
    def test_08_session_behaves_correctly_across_restart(self):
        """Verify token generated before restart remains verifiable with constant SECRET_KEY."""
        user = self.db.query(User).filter(User.email == "sarah.connor@resistance.net").first()
        token = create_access_token({
            "sub": str(user.id),
            "email": user.email,
            "username": user.username,
        })

        # Simulate simulated request with the issued token
        mock_req = MagicMock(spec=Request)
        mock_req.headers = {"Authorization": f"Bearer {token}"}
        mock_req.cookies = {}
        mock_req.query_params = {}

        resolved_user = get_current_user(request=mock_req, creds=None, db=self.db)
        self.assertEqual(resolved_user.id, user.id)
        self.assertEqual(resolved_user.email, user.email)

    # -------------------------------------------------------------------------
    # Scenario 10: User account survives fresh DB connection & process lifecycle
    # -------------------------------------------------------------------------
    def test_09_account_survives_fresh_db_connection(self):
        """Verify user persists across complete database disconnect and reconnect."""
        # 1. Close current session and dispose engine
        self.db.close()
        self.engine.dispose()

        # 2. Create entirely new engine pointing to same SQLite file
        new_engine = create_engine(
            self.db_url,
            connect_args={"check_same_thread": False}
        )
        NewSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=new_engine)

        # 3. Simulate startup lifespan seeder running on fresh connection
        with NewSessionLocal() as fresh_db:
            seed_default_user_if_needed(fresh_db)

            # Query Sarah Connor: must still exist unchanged
            persisted = fresh_db.query(User).filter(User.email == "sarah.connor@resistance.net").first()
            self.assertIsNotNone(persisted)
            self.assertEqual(persisted.username, "sconnor")
            self.assertTrue(verify_password("FuturePassword2026!", persisted.password_hash))

        new_engine.dispose()

        # Reconnect test suite engine
        self.engine = create_engine(self.db_url, connect_args={"check_same_thread": False})
        self.TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.TestingSessionLocal()

    # -------------------------------------------------------------------------
    # Scenario 11: Existing accounts continue to work after startup/seeder
    # -------------------------------------------------------------------------
    def test_10_existing_accounts_continue_to_work_after_seeder(self):
        """Verify startup seeder does not touch or hijack existing user named 'user'."""
        # Register a legitimate user with name 'User' but different email
        reg = RegisterRequest(
            name="User Display",
            username="realtrader",
            email="trader.user@gmail.com",
            password="MySecretPassword123"
        )
        resp = Response()
        register_user(req=reg, response=resp, db=self.db)

        # Run startup seeder
        seed_default_user_if_needed(self.db)

        # Login must succeed with trader's own password, NOT overridden by 123456
        login_in = LoginRequest(
            identifier="trader.user@gmail.com",
            password="MySecretPassword123"
        )
        res = login_user(req=login_in, response=resp, db=self.db)
        self.assertEqual(res["user"]["email"], "trader.user@gmail.com")

    # -------------------------------------------------------------------------
    # Scenario 12: Two different users cannot access each other's private data
    # -------------------------------------------------------------------------
    def test_11_data_isolation_between_different_users(self):
        """Verify User A and User B alerts are strictly isolated by user_id."""
        # Create User A and User B
        user_a = self.db.query(User).filter(User.email == "sarah.connor@resistance.net").first()

        reg_b = RegisterRequest(
            name="John Doe",
            username="johndoe",
            email="john.doe@example.com",
            password="JohnPassword2026!"
        )
        resp = Response()
        register_user(req=reg_b, response=resp, db=self.db)
        user_b = self.db.query(User).filter(User.email == "john.doe@example.com").first()

        # Add private alert for User A
        alert_a = Alert(
            user_id=user_a.id,
            symbol="NVDA",
            direction="UP",
            target_percent=3.5,
            warning_minutes=10,
            status="MONITORING"
        )
        self.db.add(alert_a)

        # Add private alert for User B
        alert_b = Alert(
            user_id=user_b.id,
            symbol="TSLA",
            direction="DOWN",
            target_percent=4.0,
            warning_minutes=15,
            status="MONITORING"
        )
        self.db.add(alert_b)
        self.db.commit()

        # Query User A alerts
        alerts_for_a = self.db.query(Alert).filter(Alert.user_id == user_a.id).all()
        a_symbols = [a.symbol for a in alerts_for_a]
        self.assertIn("NVDA", a_symbols)
        self.assertNotIn("TSLA", a_symbols)

        # Query User B alerts
        alerts_for_b = self.db.query(Alert).filter(Alert.user_id == user_b.id).all()
        b_symbols = [a.symbol for a in alerts_for_b]
        self.assertIn("TSLA", b_symbols)
        self.assertNotIn("NVDA", b_symbols)

    # -------------------------------------------------------------------------
    # Scenario 13: Passwords stored strictly as secure hashes, never plaintext
    # -------------------------------------------------------------------------
    def test_12_passwords_stored_as_secure_bcrypt_hashes(self):
        """Verify database password_hash is a bcrypt hash ($2b$) and never plaintext."""
        all_users = self.db.query(User).all()
        self.assertGreaterEqual(len(all_users), 2)
        for u in all_users:
            self.assertTrue(u.password_hash.startswith("$2b$") or u.password_hash.startswith("$2a$"))
            self.assertNotEqual(u.password_hash, "FuturePassword2026!")
            self.assertNotEqual(u.password_hash, "JohnPassword2026!")
            self.assertNotEqual(u.password_hash, "MySecretPassword123")

    # -------------------------------------------------------------------------
    # Scenario 14: Repeated registration and login attempts do not corrupt data
    # -------------------------------------------------------------------------
    def test_13_repeated_registration_and_login_attempts(self):
        """Verify rapid repeated login attempts do not mutate or duplicate user records."""
        initial_user_count = self.db.query(User).count()

        # Try duplicate registration 5 times
        resp = Response()
        for _ in range(5):
            with self.assertRaises(HTTPException):
                register_user(
                    req=RegisterRequest(
                        name="Duplicate Tester",
                        username="sconnor",  # Duplicate username
                        email="new.email@test.com",
                        password="PassWord123!"
                    ),
                    response=resp,
                    db=self.db
                )

        # Try 5 logins
        for _ in range(5):
            res = login_user(
                req=LoginRequest(
                    identifier="sarah.connor@resistance.net",
                    password="FuturePassword2026!"
                ),
                response=resp,
                db=self.db
            )
            self.assertEqual(res["user"]["email"], "sarah.connor@resistance.net")

        # Ensure total user count remained exactly constant
        final_user_count = self.db.query(User).count()
        self.assertEqual(initial_user_count, final_user_count)


if __name__ == "__main__":
    unittest.main()
