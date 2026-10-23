"""
StockPulse SQLite Standalone Persistence & Reconnect Lifecycle Tests
Validates:
1. Absolute database path consistency across varying working directories.
2. User registration and immediate disk commitment.
3. Engine disposal and complete connection closure.
4. Reopening the exact same SQLite file across a simulated process restart.
5. Verifying user records and bcrypt password hashes remain identical.
6. Verifying authentication and login succeed against the re-opened database.
7. Verifying updates (alerts/settings) survive reconnection without data loss.
8. Transaction rollback when a write fails.
9. Safe startup when existing database and records already exist (idempotence).
10. Safe startup behavior when database file is initially missing.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime

from fastapi import Response, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.models import Base, User, Alert, SystemSetting, Stock
from app.database.database import BASE_DIR, run_db_migrations, checkpoint_db, get_database_file_path
from app.api.auth import (
    register_user,
    login_user,
    verify_password,
    seed_default_user_if_needed,
    RegisterRequest,
    LoginRequest,
)
from app.api.stocks import seed_default_stocks_if_needed


class TestSQLitePersistenceLifecycle(unittest.TestCase):
    def setUp(self):
        # Create a dedicated temporary directory representing the persistent volume or local data/ dir
        self.temp_dir = tempfile.TemporaryDirectory(prefix="stockpulse_persist_test_")
        self.db_file = os.path.join(self.temp_dir.name, "stockpulse.db")
        self.db_url = f"sqlite:///{self.db_file.replace(chr(92), '/')}"

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_01_absolute_path_consistency(self):
        """Verifies that BASE_DIR and get_database_file_path remain absolute and consistent regardless of cwd."""
        self.assertTrue(os.path.isabs(BASE_DIR))
        expected_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.assertEqual(os.path.normcase(BASE_DIR), os.path.normcase(expected_root))

        # Test changing working directory
        orig_cwd = os.getcwd()
        try:
            os.chdir(self.temp_dir.name)
            resolved_path = get_database_file_path()
            self.assertTrue(os.path.isabs(resolved_path))
            self.assertTrue(resolved_path.endswith("stockpulse.db"))
        finally:
            os.chdir(orig_cwd)

    def test_02_register_close_reopen_and_login(self):
        """
        Step-by-step lifecycle test:
        1. Register a user on Connection 1
        2. Close session, dispose engine (complete disconnect)
        3. Reopen Connection 2 to the same SQLite file
        4. Run startup migrations and seeder (simulating app boot)
        5. Verify user, email, and password hash are preserved
        6. Verify login works with the original credentials
        """
        # Connection 1: Create schema and register user
        engine1 = create_engine(
            self.db_url,
            connect_args={"check_same_thread": False, "timeout": 15},
        )
        Base.metadata.create_all(bind=engine1)
        run_db_migrations(engine1)
        Session1 = sessionmaker(bind=engine1)

        raw_password = "StrictLocalPassword#2026"
        email = "investor.john@example.com"
        username = "john_trader"
        name = "John Trader"

        with Session1() as db1:
            req = RegisterRequest(
                name=name,
                username=username,
                email=email,
                password=raw_password,
            )
            resp = Response()
            reg_result = register_user(req=req, response=resp, db=db1)
            self.assertEqual(reg_result["status"], "SUCCESS")

            # Check that user was written
            user1 = db1.query(User).filter_by(email=email).first()
            self.assertIsNotNone(user1)
            user_id = user1.id
            saved_hash = user1.password_hash
            self.assertTrue(saved_hash.startswith("$2b$"))

        # Checkpoint WAL and completely disconnect
        checkpoint_db(engine1)
        engine1.dispose()

        # Connection 2: Reopen the database from scratch (Simulating server restart)
        self.assertTrue(os.path.exists(self.db_file))
        self.assertGreater(os.path.getsize(self.db_file), 0)

        engine2 = create_engine(
            self.db_url,
            connect_args={"check_same_thread": False, "timeout": 15},
        )
        # Simulate lifespan startup
        Base.metadata.create_all(bind=engine2)
        run_db_migrations(engine2)
        Session2 = sessionmaker(bind=engine2)

        with Session2() as db2:
            # Run startup seeders (must NOT overwrite or alter existing user)
            seed_default_user_if_needed(db2)

            # Query existing user on new connection
            user2 = db2.query(User).filter_by(id=user_id).first()
            self.assertIsNotNone(user2, "User record disappeared after reconnect!")
            self.assertEqual(user2.email, email)
            self.assertEqual(user2.username, username)
            self.assertEqual(user2.name, name)
            self.assertEqual(user2.password_hash, saved_hash)

            # Verify bcrypt hash directly
            self.assertTrue(verify_password(raw_password, user2.password_hash))
            self.assertFalse(verify_password("WrongPassword!", user2.password_hash))

            # Verify login endpoint succeeds
            login_req = LoginRequest(
                username=email,
                password=raw_password,
            )
            login_resp = Response()
            login_res = login_user(req=login_req, response=login_resp, db=db2)
            self.assertEqual(login_res["status"], "SUCCESS")
            self.assertIn("access_token", login_res)
            self.assertEqual(login_res["user"]["email"], email)
            self.assertEqual(login_res["user"]["username"], username)

        engine2.dispose()

    def test_03_alerts_and_data_persistence_across_reconnect(self):
        """Verifies alerts, settings, and relations persist across database disconnect."""
        # Connection 1: Register user and create alert
        engine1 = create_engine(self.db_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine1)
        Session1 = sessionmaker(bind=engine1)

        with Session1() as db1:
            u = User(
                name="Alert Tester",
                username="alerttester",
                email="alert@stockpulse.local",
                password_hash="$2b$12$eMockHashForTestingAlertPersistenceOnly",
                is_verified=True,
            )
            db1.add(u)
            db1.commit()
            db1.refresh(u)

            alert = Alert(
                user_id=u.id,
                symbol="TCS",
                direction="UP",
                target_percent=3.0,
                warning_minutes=15,
                status="MONITORING",
            )
            db1.add(alert)

            setting = SystemSetting(
                key="market_regime",
                value="BULLISH",
                description="Current market regime",
            )
            db1.add(setting)
            db1.commit()
            alert_id = alert.id

        checkpoint_db(engine1)
        engine1.dispose()

        # Connection 2: Reopen
        engine2 = create_engine(self.db_url, connect_args={"check_same_thread": False})
        Session2 = sessionmaker(bind=engine2)

        with Session2() as db2:
            alert2 = db2.query(Alert).filter_by(id=alert_id).first()
            self.assertIsNotNone(alert2)
            self.assertEqual(alert2.symbol, "TCS")
            self.assertEqual(alert2.target_percent, 3.0)
            self.assertEqual(alert2.user.email, "alert@stockpulse.local")

            setting2 = db2.query(SystemSetting).filter_by(key="market_regime").first()
            self.assertIsNotNone(setting2)
            self.assertEqual(setting2.value, "BULLISH")

        engine2.dispose()

    def test_04_transaction_rollback_on_write_failure(self):
        """Verifies that an aborted/failed write transaction cleanly rolls back without corrupting the session."""
        engine = create_engine(self.db_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)

        with Session() as db:
            # Insert valid user
            u1 = User(
                name="Valid User",
                username="valid_user",
                email="valid@stockpulse.local",
                password_hash="some_hash",
            )
            db.add(u1)
            db.commit()

            # Attempt to insert duplicate user with same email (violates unique constraint)
            u2 = User(
                name="Duplicate User",
                username="dup_user",
                email="valid@stockpulse.local",
                password_hash="some_hash2",
            )
            db.add(u2)
            with self.assertRaises(IntegrityError):
                db.commit()

            # Rollback transaction
            db.rollback()

            # Verify only u1 exists and session is healthy for subsequent queries
            user_count = db.query(User).filter_by(email="valid@stockpulse.local").count()
            self.assertEqual(user_count, 1)

            # Subsequent valid write succeeds on the same session
            setting = SystemSetting(key="health_check", value="ok")
            db.add(setting)
            db.commit()
            self.assertEqual(db.query(SystemSetting).filter_by(key="health_check").first().value, "ok")

        engine.dispose()

    def test_05_safe_startup_with_existing_database_and_records(self):
        """Verifies that multiple startup lifecycles are idempotent and do not wipe or overwrite data."""
        engine = create_engine(self.db_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)

        # 1. Initial seed & user
        with Session() as db:
            seed_default_stocks_if_needed(db)
            seed_default_user_if_needed(db)
            custom_user = User(
                name="Custom Trader",
                username="customtrader",
                email="custom@trader.io",
                password_hash="$2b$12$PreservedCustomHash1234567890",
            )
            db.add(custom_user)
            db.commit()

            stocks_before = db.query(Stock).count()
            users_before = db.query(User).count()

        # 2. Simulate 3 consecutive server restarts running startup routines
        for _ in range(3):
            Base.metadata.create_all(bind=engine)
            run_db_migrations(engine)
            with Session() as db:
                seed_default_stocks_if_needed(db)
                seed_default_user_if_needed(db)

                # Ensure counts and records remain exactly the same
                self.assertEqual(db.query(Stock).count(), stocks_before)
                self.assertEqual(db.query(User).count(), users_before)

                user = db.query(User).filter_by(email="custom@trader.io").first()
                self.assertIsNotNone(user)
                self.assertEqual(user.password_hash, "$2b$12$PreservedCustomHash1234567890")

        engine.dispose()

    def test_06_startup_behavior_when_database_is_missing(self):
        """Verifies clean initialization when starting from a completely non-existent database file."""
        missing_db_file = os.path.join(self.temp_dir.name, "subfolder", "new_stockpulse.db")
        self.assertFalse(os.path.exists(missing_db_file))

        missing_db_url = f"sqlite:///{missing_db_file.replace(chr(92), '/')}"
        os.makedirs(os.path.dirname(missing_db_file), exist_ok=True)

        engine = create_engine(missing_db_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        run_db_migrations(engine)
        Session = sessionmaker(bind=engine)

        with Session() as db:
            seed_default_stocks_if_needed(db)
            seed_default_user_if_needed(db)
            self.assertGreater(db.query(Stock).count(), 0)
            self.assertIsNotNone(db.query(User).filter_by(email="user@stockpulse.local").first())

        self.assertTrue(os.path.exists(missing_db_file))
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
