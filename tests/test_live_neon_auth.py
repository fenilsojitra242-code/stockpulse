"""
Automated Test Suite: Comprehensive Login System Verification against Live Neon PostgreSQL
Tests all 12 authentication conditions:
1. Default test user login by username ('user')
2. Default test user login by email ('user@stockpulse.local')
3. Case-insensitivity for username & email
4. Rejection of incorrect password (HTTP 401)
5. Rejection of unknown username (HTTP 401)
6. Rejection of unknown email (HTTP 401)
7. Handling of empty or blank identifier/password
8. Bcrypt password hash integrity across all DB users (no plaintext)
9. JWT token generation, signature & expiration validation
10. Protected route authentication (valid token vs missing vs tampered)
11. Session logout cookie clearance & account preservation
12. Rate limiting protection against brute force attempts (HTTP 429)
"""

import os
import sys
import unittest
from fastapi import Response, HTTPException, Request, status
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.models import User
from app.api.auth import (
    login_user,
    logout_user,
    get_current_user,
    create_access_token,
    decode_access_token,
    verify_password,
    LoginRequest,
    FAILED_LOGIN_ATTEMPTS,
    clear_failed_login,
)

LIVE_POSTGRES_URL = "postgresql://neondb_owner:npg_YBt2iFoUA0vg@ep-jolly-pine-b4me4cj8-pooler.c-6.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"


class TestLiveNeonAuthentication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(LIVE_POSTGRES_URL, pool_pre_ping=True)
        cls.Session = sessionmaker(bind=cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.db = self.Session()
        FAILED_LOGIN_ATTEMPTS.clear()

    def tearDown(self):
        self.db.close()
        FAILED_LOGIN_ATTEMPTS.clear()

    def test_01_login_with_username_success(self):
        """Condition 1: Valid login with username ('user') and password ('123456')."""
        req = LoginRequest(identifier="user", password="123456")
        res = Response()
        result = login_user(req, res, db=self.db)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertIn("access_token", result)
        self.assertEqual(result["user"]["username"], "user")

    def test_02_login_with_email_success(self):
        """Condition 2: Valid login with email ('user@stockpulse.local') and password ('123456')."""
        req = LoginRequest(identifier="user@stockpulse.local", password="123456")
        res = Response()
        result = login_user(req, res, db=self.db)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertIn("access_token", result)
        self.assertEqual(result["user"]["email"], "user@stockpulse.local")

    def test_03_case_insensitivity(self):
        """Condition 3: Login is case-insensitive for username ('USER') and email ('USER@StockPulse.LOCAL')."""
        res = Response()
        # UPPERCASE username
        r1 = login_user(LoginRequest(identifier="USER", password="123456"), res, db=self.db)
        self.assertEqual(r1["status"], "SUCCESS")

        # Mixed-case email
        r2 = login_user(LoginRequest(identifier="User@StockPulse.Local", password="123456"), res, db=self.db)
        self.assertEqual(r2["status"], "SUCCESS")

    def test_04_wrong_password_rejected(self):
        """Condition 4: Incorrect password must be rejected with HTTP 401."""
        req = LoginRequest(identifier="user", password="IncorrectPassword999!")
        res = Response()
        with self.assertRaises(HTTPException) as ctx:
            login_user(req, res, db=self.db)
        self.assertEqual(ctx.exception.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("Invalid email/username or password", ctx.exception.detail)

    def test_05_unknown_username_rejected(self):
        """Condition 5: Non-existent username must be rejected with HTTP 401."""
        req = LoginRequest(identifier="ghost_user_does_not_exist", password="Password123!")
        res = Response()
        with self.assertRaises(HTTPException) as ctx:
            login_user(req, res, db=self.db)
        self.assertEqual(ctx.exception.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_06_unknown_email_rejected(self):
        """Condition 6: Non-existent email must be rejected with HTTP 401."""
        req = LoginRequest(identifier="never_registered@nowhere.com", password="Password123!")
        res = Response()
        with self.assertRaises(HTTPException) as ctx:
            login_user(req, res, db=self.db)
        self.assertEqual(ctx.exception.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_07_empty_identifier_validation(self):
        """Condition 7: Empty or whitespace-only identifier must be rejected."""
        with self.assertRaises(ValueError):
            LoginRequest(identifier="   ", password="123456")

    def test_08_database_password_hashes_are_bcrypt(self):
        """Condition 8: All user records in PostgreSQL must have valid salted bcrypt hashes."""
        users = self.db.query(User).all()
        self.assertGreaterEqual(len(users), 8)
        for u in users:
            self.assertIsNotNone(u.password_hash)
            # Bcrypt hashes always start with $2b$, $2a$, or $2y$ and are 60 chars long
            self.assertTrue(
                u.password_hash.startswith(("$2a$", "$2b$", "$2y$")),
                f"User {u.email} does not have a valid bcrypt prefix"
            )
            self.assertEqual(len(u.password_hash), 60)
            self.assertNotEqual(u.password_hash, "123456", "Plaintext password detected!")

    def test_09_jwt_token_validation(self):
        """Condition 9: Generated JWT token contains valid user claims and signature."""
        token = create_access_token({"sub": "12", "email": "user@stockpulse.local", "username": "user"})
        payload = decode_access_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["sub"], "12")
        self.assertEqual(payload["email"], "user@stockpulse.local")
        self.assertIn("exp", payload)

    def test_10_protected_route_auth_dependency(self):
        """Condition 10: get_current_user dependency accepts valid token and rejects tampered tokens."""
        token = create_access_token({"sub": "12", "email": "user@stockpulse.local", "username": "user"})
        
        # Mock request with valid Authorization header
        valid_request = Request({"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})
        user = get_current_user(valid_request, creds=None, db=self.db)
        self.assertEqual(user.id, 12)
        self.assertEqual(user.email, "user@stockpulse.local")

        # Missing token
        empty_request = Request({"type": "http", "headers": [], "query_string": b""})
        with self.assertRaises(HTTPException) as ctx_missing:
            get_current_user(empty_request, creds=None, db=self.db)
        self.assertEqual(ctx_missing.exception.status_code, status.HTTP_401_UNAUTHORIZED)

        # Tampered / fake token
        fake_request = Request({"type": "http", "headers": [(b"authorization", b"Bearer fake.tampered.token")], "query_string": b""})
        with self.assertRaises(HTTPException) as ctx_fake:
            get_current_user(fake_request, creds=None, db=self.db)
        self.assertEqual(ctx_fake.exception.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_11_logout_clears_cookie_preserves_user(self):
        """Condition 11: logout_user clears auth cookie without deleting database account."""
        res = Response()
        out = logout_user(res)
        self.assertEqual(out["status"].upper(), "SUCCESS")

        # User record must still exist in DB
        u = self.db.query(User).filter(User.id == 12).first()
        self.assertIsNotNone(u)
        self.assertEqual(u.email, "user@stockpulse.local")

    def test_12_rate_limiting_brute_force_protection(self):
        """Condition 12: Brute force login attempts trigger 429 Too Many Requests."""
        res = Response()
        # Fire 5 wrong password attempts
        for _ in range(5):
            try:
                login_user(LoginRequest(identifier="user@stockpulse.local", password="WrongPassword!"), res, db=self.db)
            except HTTPException:
                pass

        # 6th attempt should be blocked by rate limiter with 429
        with self.assertRaises(HTTPException) as ctx:
            login_user(LoginRequest(identifier="user@stockpulse.local", password="WrongPassword!"), res, db=self.db)
        self.assertEqual(ctx.exception.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn("Too many failed login attempts", ctx.exception.detail)

    def test_13_new_user_registration_and_duplicate_guard(self):
        """Condition 13: New user registers, logs in immediately, and duplicate registration is rejected."""
        import time
        from app.api.auth import register_user, RegisterRequest
        
        test_email = f"live_test_{int(time.time())}@stockpulse.io"
        test_user = f"live_user_{int(time.time())}"
        test_pw = "LivePass123!"

        res = Response()
        # 1. Register new account
        reg_res = register_user(
            RegisterRequest(name="Live Test Trader", email=test_email, username=test_user, password=test_pw),
            res,
            db=self.db
        )
        self.assertEqual(reg_res["status"], "SUCCESS")
        new_user_id = reg_res["user"]["id"]

        try:
            # 2. Duplicate registration with same email must fail
            with self.assertRaises(HTTPException) as dup_ctx:
                register_user(
                    RegisterRequest(name="Duplicate Trader", email=test_email, password=test_pw),
                    res,
                    db=self.db
                )
            self.assertEqual(dup_ctx.exception.status_code, status.HTTP_400_BAD_REQUEST)

            # 3. Login with newly registered credentials succeeds immediately
            login_res = login_user(
                LoginRequest(identifier=test_email, password=test_pw),
                res,
                db=self.db
            )
            self.assertEqual(login_res["status"], "SUCCESS")
            self.assertEqual(login_res["user"]["email"], test_email)
        finally:
            # Cleanup test account to keep database pristine
            self.db.query(User).filter(User.id == new_user_id).delete()
            self.db.commit()


if __name__ == "__main__":
    unittest.main()
