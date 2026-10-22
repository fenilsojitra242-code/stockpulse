"""
StockPulse User Re-Login and Session Architecture Regression Test Suite
Validates the complete registration, login, session, and persistence flow.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from fastapi import Response, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.models import Base, User, Alert
from app.api.auth import (
    register_user,
    login_user,
    logout_user,
    get_current_user,
    get_current_user_profile,
    create_access_token,
    decode_access_token,
    get_token_from_request,
    verify_password,
    hash_password,
    seed_default_user_if_needed,
    RegisterRequest,
    LoginRequest,
    COOKIE_NAME,
)


class TestReLoginRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.temp_dir.name, "test_relogin.db")
        cls.db_url = f"sqlite:///{cls.db_path}"
        cls.engine = create_engine(
            cls.db_url,
            connect_args={"check_same_thread": False}
        )
        cls.TestingSessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=cls.engine
        )
        Base.metadata.create_all(bind=cls.engine)

        with cls.TestingSessionLocal() as db:
            seed_default_user_if_needed(db)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        try:
            cls.temp_dir.cleanup()
        except Exception:
            pass

    def setUp(self):
        self.db = self.TestingSessionLocal()

    def tearDown(self):
        self.db.close()

    # -------------------------------------------------------------------------
    # Failure Check 1: Model validation on empty identifier
    # -------------------------------------------------------------------------
    def test_01_login_request_requires_identifier_or_email_or_username(self):
        """Verify LoginRequest rejects payloads missing an identifier, email, or username."""
        # Payload with empty identifier and no email/username must fail validation
        with self.assertRaises((ValueError, HTTPException)):
            LoginRequest(identifier="", password="ValidPassword123")

        with self.assertRaises((ValueError, HTTPException)):
            LoginRequest(password="ValidPassword123")

    # -------------------------------------------------------------------------
    # Failure Check 2: Token extractor must not return 'undefined' or 'null'
    # -------------------------------------------------------------------------
    def test_02_token_extractor_handles_undefined_and_falls_back_to_cookie(self):
        """When Authorization is Bearer undefined, extractor must fall back to cookie."""
        mock_req = MagicMock(spec=Request)
        mock_req.headers = {"Authorization": "Bearer undefined"}
        mock_req.cookies = {COOKIE_NAME: "valid.session.cookie.token"}
        mock_req.query_params = {}
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="undefined")

        token = get_token_from_request(mock_req, creds)
        self.assertEqual(token, "valid.session.cookie.token")

    # -------------------------------------------------------------------------
    # Failure Check 3: Exact account lookup without ambiguous fallbacks
    # -------------------------------------------------------------------------
    def test_03_exact_account_lookup_without_ambiguous_fallbacks(self):
        """Verify login does not match by ambiguous display-name or email-prefix."""
        # Create User Alpha
        reg_a = RegisterRequest(
            name="Developer John",
            username="devjohn",
            email="devjohn@enterprise.com",
            password="AlphaPassword123",
            confirm_password="AlphaPassword123"
        )
        resp_a = Response()
        register_user(req=reg_a, response=resp_a, db=self.db)

        # Create User Beta with the same display name
        reg_b = RegisterRequest(
            name="Developer John",
            username="johndeveloper",
            email="john.developer@other.com",
            password="BetaPassword456",
            confirm_password="BetaPassword456"
        )
        resp_b = Response()
        register_user(req=reg_b, response=resp_b, db=self.db)

        # 1. Login with display name "Developer John" must be REJECTED (exact email or username only)
        resp = Response()
        with self.assertRaises(HTTPException) as ctx:
            login_user(
                req=LoginRequest(identifier="Developer John", password="AlphaPassword123"),
                response=resp,
                db=self.db
            )
        self.assertEqual(ctx.exception.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Login with email prefix "devjohn" must NOT match if someone used "devjohn" as email prefix of someone else
        # It only matches exact username "devjohn" or exact email
        resp1 = Response()
        res1 = login_user(
            req=LoginRequest(identifier="devjohn", password="AlphaPassword123"),
            response=resp1,
            db=self.db
        )
        self.assertEqual(res1["user"]["username"], "devjohn")

    # -------------------------------------------------------------------------
    # Complete 10-Step Sequence
    # -------------------------------------------------------------------------
    def test_04_complete_relogin_sequence_all_10_steps(self):
        """
        Executes all 10 requirements:
        1. Register new user.
        2. Confirm user in database.
        3. Fresh client session / logout.
        4. Log in with registered email & original password.
        5. Log in with registered username & original password.
        6. Reject incorrect password.
        7. Access protected endpoints.
        8. Page refresh preserves session (token and cookie).
        9. Existing users can still log in.
        10. Registration and login use same DB after restart.
        """
        email = "relogin_candidate@stockpulse.local"
        username = "relogin_candidate"
        password = "SecurePassword2026!"
        name = "Candidate Trader"

        # 1. Register a new user
        reg_req = RegisterRequest(
            name=name,
            username=username,
            email=email,
            password=password,
            confirm_password=password
        )
        resp = Response()
        reg_result = register_user(req=reg_req, response=resp, db=self.db)
        self.assertEqual(reg_result["status"], "SUCCESS")
        user_id = reg_result["user"]["id"]

        # 2. Confirm the user exists in database
        db_user = self.db.query(User).filter(User.id == user_id).first()
        self.assertIsNotNone(db_user)
        self.assertEqual(db_user.email, email.lower())
        self.assertEqual(db_user.username, username.lower())
        self.assertTrue(db_user.password_hash.startswith("$2b$"))
        self.assertTrue(verify_password(password, db_user.password_hash))

        # 3. Log out or start a fresh client session
        logout_resp = Response()
        logout_result = logout_user(response=logout_resp)
        self.assertEqual(logout_result["status"], "SUCCESS")

        # 4. Log in using registered email and original password
        login_email_resp = Response()
        res_email = login_user(
            req=LoginRequest(identifier=email, password=password),
            response=login_email_resp,
            db=self.db
        )
        self.assertEqual(res_email["status"], "SUCCESS")
        self.assertEqual(res_email["user"]["id"], user_id)
        email_token = res_email["access_token"]
        self.assertIsNotNone(email_token)

        # Also test with 'email' field in payload
        res_email_field = login_user(
            req=LoginRequest(email=email.upper(), password=password),
            response=Response(),
            db=self.db
        )
        self.assertEqual(res_email_field["user"]["id"], user_id)

        # 5. Repeat with registered username and original password
        login_user_resp = Response()
        res_username = login_user(
            req=LoginRequest(identifier=username.upper(), password=password),
            response=login_user_resp,
            db=self.db
        )
        self.assertEqual(res_username["status"], "SUCCESS")
        self.assertEqual(res_username["user"]["id"], user_id)
        user_token = res_username["access_token"]

        # Also test with 'username' field in payload
        res_user_field = login_user(
            req=LoginRequest(username=username, password=password),
            response=Response(),
            db=self.db
        )
        self.assertEqual(res_user_field["user"]["id"], user_id)

        # 6. Verify an incorrect password is rejected
        with self.assertRaises(HTTPException) as bad_pw_ctx:
            login_user(
                req=LoginRequest(identifier=email, password="WrongPassword!"),
                response=Response(),
                db=self.db
            )
        self.assertEqual(bad_pw_ctx.exception.status_code, status.HTTP_401_UNAUTHORIZED)

        # 7. Verify successful login grants access to protected endpoints
        mock_req = MagicMock(spec=Request)
        mock_req.headers = {"Authorization": f"Bearer {user_token}"}
        mock_req.cookies = {}
        mock_req.query_params = {}
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=user_token)

        current_u = get_current_user(request=mock_req, creds=creds, db=self.db)
        self.assertEqual(current_u.id, user_id)
        profile = get_current_user_profile(current_user=current_u, db=self.db)
        self.assertTrue(profile["logged_in"])
        self.assertEqual(profile["user"]["email"], email)

        # 8. Verify page refresh preserves the session as intended (via cookie or token)
        cookie_req = MagicMock(spec=Request)
        cookie_req.headers = {}
        cookie_req.cookies = {COOKIE_NAME: user_token}
        cookie_req.query_params = {}

        cookie_user = get_current_user(request=cookie_req, creds=None, db=self.db)
        self.assertEqual(cookie_user.id, user_id)

        # 9. Verify existing users can still log in
        default_login = login_user(
            req=LoginRequest(identifier="user", password="123456"),
            response=Response(),
            db=self.db
        )
        self.assertEqual(default_login["user"]["username"], "user")

        # 10. Verify registration and login use the same database after an application restart
        self.db.close()
        self.engine.dispose()

        # Restart simulation: new engine & session connected to the exact same DB file
        restarted_engine = create_engine(self.db_url, connect_args={"check_same_thread": False})
        RestartedSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=restarted_engine)

        with RestartedSessionLocal() as restarted_db:
            seed_default_user_if_needed(restarted_db)
            relogin = login_user(
                req=LoginRequest(identifier=username, password=password),
                response=Response(),
                db=restarted_db
            )
            self.assertEqual(relogin["user"]["id"], user_id)
            self.assertEqual(relogin["user"]["email"], email)

        restarted_engine.dispose()
        # Restore test suite engine
        self.engine = create_engine(self.db_url, connect_args={"check_same_thread": False})
        self.TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.TestingSessionLocal()


if __name__ == "__main__":
    unittest.main()
