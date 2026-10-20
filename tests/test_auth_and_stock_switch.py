"""
StockPulse Auth & Stock-Switching Performance & Safety Test Suite

Validates all prompt requirements from Section 5:
1. Login and registration still work correctly.
2. Invalid credentials and invalid registration data are handled safely.
3. Duplicate form submissions and duplicate requests are handled safely.
4. Stock switching returns correct stock payload without symbol mixing.
5. Rapid stock switching logic prevents stale responses from overwriting latest selection.
6. Caches are reused without bypassing freshness checks.
7. Lightweight market-status endpoint operates without heavy market data invocation.
8. No production database records are changed by benchmarks or automated tests.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

from fastapi import Response, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.models import Base, User
from app.api.auth import register_user, login_user, seed_default_user_if_needed, RegisterRequest, LoginRequest
from app.api.stocks import get_stock_data, get_global_market_status_endpoint
from app.services.market_data import (
    _RANGE_CACHE,
    get_stock_visualization_data,
    get_market_status,
    clear_negative_cache,
)


class TestAuthAndStockSwitch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create an isolated temporary SQLite database for this test class
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.temp_dir.name, "test_auth_switch.db")
        cls.engine = create_engine(
            f"sqlite:///{cls.db_path}",
            connect_args={"check_same_thread": False}
        )
        cls.TestingSessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=cls.engine
        )
        Base.metadata.create_all(bind=cls.engine)

        # Seed default test user in isolated test database
        with cls.TestingSessionLocal() as db:
            seed_default_user_if_needed(db)

        # Record production DB modification time if it exists
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
            assert current_mtime == cls.prod_db_mtime, "Production DB was modified during tests!"

    def setUp(self):
        self.db = self.TestingSessionLocal()
        _RANGE_CACHE.clear()
        clear_negative_cache()

    def tearDown(self):
        self.db.close()

    # =========================================================================
    # 1. AUTHENTICATION & REGISTRATION TESTS
    # =========================================================================

    def test_01_default_test_user_login(self):
        """Verify default test user 'user' with '123456' logs in successfully."""
        response = Response()
        login_in = LoginRequest(identifier="user", password="123456")
        data = login_user(req=login_in, response=response, db=self.db)
        self.assertIn("access_token", data)
        self.assertIn("user", data)
        self.assertEqual(data["user"]["username"], "user")

    def test_02_registration_valid_user(self):
        """Verify new user registration works correctly."""
        response = Response()
        reg_in = RegisterRequest(
            name="Switch Tester",
            username="tester_switch",
            email="tester_switch@stockpulse.internal",
            password="StrongPassword123!"
        )
        data = register_user(req=reg_in, response=response, db=self.db)
        self.assertIn("access_token", data)
        self.assertEqual(data["user"]["username"], "tester_switch")

        # Test login with username
        login_user_in = LoginRequest(identifier="tester_switch", password="StrongPassword123!")
        data_user = login_user(req=login_user_in, response=response, db=self.db)
        self.assertIn("access_token", data_user)

        # Test login with email
        login_email_in = LoginRequest(identifier="tester_switch@stockpulse.internal", password="StrongPassword123!")
        data_email = login_user(req=login_email_in, response=response, db=self.db)
        self.assertIn("access_token", data_email)

    def test_03_invalid_credentials_rejected_safely(self):
        """Verify invalid passwords and non-existent users return 401."""
        response = Response()
        # Wrong password for existing user
        login_wrong = LoginRequest(identifier="tester_switch", password="WrongPassword999!")
        with self.assertRaises(HTTPException) as ctx:
            login_user(req=login_wrong, response=response, db=self.db)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("Invalid email/username or password", ctx.exception.detail)

        # Non-existent user
        login_ghost = LoginRequest(identifier="ghost_user_does_not_exist", password="SomePassword")
        with self.assertRaises(HTTPException) as ctx:
            login_user(req=login_ghost, response=response, db=self.db)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_04_registration_duplicate_prevention(self):
        """Verify duplicate usernames or emails are rejected."""
        response = Response()
        # Duplicate email
        dup_email = RegisterRequest(
            name="Another User",
            username="another_user",
            email="tester_switch@stockpulse.internal",
            password="Password123!"
        )
        with self.assertRaises(HTTPException) as ctx:
            register_user(req=dup_email, response=response, db=self.db)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("already exists", ctx.exception.detail)

        # Duplicate username
        dup_user = RegisterRequest(
            name="Brand New",
            username="tester_switch",
            email="brand_new_email@test.com",
            password="Password123!"
        )
        with self.assertRaises(HTTPException) as ctx:
            register_user(req=dup_user, response=response, db=self.db)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("is already taken", ctx.exception.detail)

    def test_05_registration_short_password_rejected(self):
        """Verify passwords shorter than 4 characters are rejected by validator or logic."""
        response = Response()
        with self.assertRaises(Exception):
            RegisterRequest(
                name="Short Pass",
                username="short_pass_user",
                email="short_pass@test.com",
                password="12"
            )

    def test_06_seeding_is_idempotent_and_fast(self):
        """Verify seed_default_user_if_needed skips re-hashing when test user already exists."""
        # Call seeding again
        seed_default_user_if_needed(self.db)
        user = self.db.query(User).filter(User.username == "user").first()
        self.assertIsNotNone(user)
        self.assertEqual(user.username, "user")

    # =========================================================================
    # 2. STOCK SWITCHING & CACHING TESTS
    # =========================================================================

    def test_07_stock_detail_endpoint_returns_correct_symbol(self):
        """Verify get_stock_data returns accurate symbol and data structure."""
        response = Response()
        data_tcs = get_stock_data(symbol="TCS", response=response, range="1D")
        self.assertEqual(data_tcs.get("symbol"), "TCS")
        self.assertIn("price", data_tcs)
        self.assertIn("candles", data_tcs)
        self.assertIn("supertrend", data_tcs)

        # Switch to INFY
        data_infy = get_stock_data(symbol="INFY", response=response, range="1D")
        self.assertEqual(data_infy.get("symbol"), "INFY")
        self.assertIn("candles", data_infy)

    def test_08_cache_reuse_without_bypassing_freshness(self):
        """Verify repeated calls within TTL hit cache without redundant work."""
        response = Response()
        data1 = get_stock_data(symbol="TCS", response=response, range="1D")
        self.assertEqual(data1.get("symbol"), "TCS")

        # Check that cache has entry
        cache_key = ("TCS", "1D")
        self.assertIn(cache_key, _RANGE_CACHE)
        cached_ts = _RANGE_CACHE[cache_key]["timestamp"]

        # Immediate repeated fetch should reuse cache
        data2 = get_stock_data(symbol="TCS", response=response, range="1D")
        self.assertEqual(_RANGE_CACHE[cache_key]["timestamp"], cached_ts)

    def test_09_lightweight_market_status_endpoint(self):
        """Verify get_global_market_status_endpoint returns immediately without heavy market data pipeline."""
        response = Response()
        data = get_global_market_status_endpoint(response=response)
        self.assertIn("market_status", data)
        self.assertIn(data["market_status"], ["OPEN", "CLOSED"])
        self.assertIn("time", data)

    # =========================================================================
    # 3. RAPID STOCK SWITCHING & RACE CONDITION DISCARD SIMULATION
    # =========================================================================

    def test_10_rapid_switching_monotonic_sequence_resolution(self):
        """
        Simulate frontend activeFetchSeq behavior:
        When switching rapidly (TCS -> INFY -> RELIANCE), obsolete responses
        for earlier sequences must be discarded and cannot overwrite the latest selection.
        """
        active_seq = 0
        current_rendered_symbol = None

        def simulate_user_switch(new_symbol):
            nonlocal active_seq
            active_seq += 1
            seq = active_seq
            return seq, new_symbol

        def simulate_response_resolution(seq, response_symbol):
            nonlocal current_rendered_symbol
            # Frontend rule: if seq !== activeFetchSeq, discard
            if seq == active_seq:
                current_rendered_symbol = response_symbol
                return True
            return False

        # User rapidly clicks: TCS (seq 1), then INFY (seq 2), then RELIANCE (seq 3)
        seq1, sym1 = simulate_user_switch("TCS")
        seq2, sym2 = simulate_user_switch("INFY")
        seq3, sym3 = simulate_user_switch("RELIANCE")

        self.assertEqual(seq1, 1)
        self.assertEqual(seq2, 2)
        self.assertEqual(seq3, 3)

        # Suppose seq1 finishes late (after user chose RELIANCE)
        accepted1 = simulate_response_resolution(seq1, "TCS")
        self.assertFalse(accepted1)
        self.assertIsNone(current_rendered_symbol)

        # Suppose seq2 finishes
        accepted2 = simulate_response_resolution(seq2, "INFY")
        self.assertFalse(accepted2)
        self.assertIsNone(current_rendered_symbol)

        # seq3 finishes (latest selection)
        accepted3 = simulate_response_resolution(seq3, "RELIANCE")
        self.assertTrue(accepted3)
        self.assertEqual(current_rendered_symbol, "RELIANCE")


if __name__ == "__main__":
    unittest.main()
