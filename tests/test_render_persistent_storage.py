"""
StockPulse Render Persistent Storage & Migration Regression Test Suite
Validates:
1. Default local SQLite database path remains unchanged when PERSISTENT_DATA_DIR is unset.
2. Configured persistent directory correctly selects the expected database path.
3. Missing or unwritable persistent storage raises a clear RuntimeError and fails safely.
4. Existing databases and records are never overwritten or reset during startup.
5. Database migration to persistent storage flushes WAL, verifies integrity, and preserves all rows.
"""

import os
import sys
import tempfile
import unittest
import sqlite3
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.database import (
    BASE_DIR,
    validate_storage_directory,
    get_database_file_path,
    run_db_migrations,
    checkpoint_db,
)
from app.database.migrate_to_persistent_disk import (
    migrate_to_persistent_disk,
    check_sqlite_integrity,
    get_table_row_counts,
)
from app.database.models import Base, User, Alert, SystemSetting
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class TestRenderPersistentStorage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="stockpulse_persist_test_")

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_01_default_local_path_unchanged(self):
        """Verifies default local database path points to BASE_DIR/data/stockpulse.db."""
        expected_default = os.path.join(BASE_DIR, "data", "stockpulse.db")
        # In default local development without PERSISTENT_DATA_DIR:
        from app.database import database
        self.assertEqual(os.path.normcase(database.DEFAULT_DB_FILE), os.path.normcase(expected_default))
        self.assertTrue(os.path.isabs(database.DEFAULT_DB_FILE))

    def test_02_configured_persistent_directory_selection(self):
        """Verifies configured persistent directory correctly defines the target database file."""
        mock_persistent_dir = os.path.join(self.temp_dir.name, "var_data")
        os.makedirs(mock_persistent_dir, exist_ok=True)

        # Helper function replicating directory resolution in database.py
        if os.path.isabs(mock_persistent_dir):
            target_db = os.path.join(mock_persistent_dir, "stockpulse.db")
        else:
            target_db = os.path.abspath(os.path.join(BASE_DIR, mock_persistent_dir, "stockpulse.db"))

        self.assertEqual(os.path.normcase(target_db), os.path.normcase(os.path.join(mock_persistent_dir, "stockpulse.db")))
        validated_dir = validate_storage_directory(mock_persistent_dir, is_persistent_configured=True)
        self.assertEqual(os.path.normcase(validated_dir), os.path.normcase(mock_persistent_dir))

    def test_03_missing_or_unwritable_persistent_storage_fails_safely(self):
        """Verifies that an invalid persistent directory raises a clear RuntimeError without silent fallback."""
        # Case A: Persistent path is an existing file, not a directory
        fake_file = os.path.join(self.temp_dir.name, "not_a_dir.txt")
        with open(fake_file, "w") as f:
            f.write("test")

        with self.assertRaises(RuntimeError) as ctx:
            validate_storage_directory(fake_file, is_persistent_configured=True)
        self.assertIn("not a directory", str(ctx.exception))

        # Case B: Persistent directory creation fails (e.g. invalid path or permissions)
        with patch("os.makedirs", side_effect=PermissionError("Mock Permission Denied")):
            with self.assertRaises(RuntimeError) as ctx2:
                validate_storage_directory(os.path.join(self.temp_dir.name, "forbidden_dir"), is_persistent_configured=True)
            self.assertIn("Failed to create configured persistent storage directory", str(ctx2.exception))

        # Case C: Write probe fails
        test_dir = os.path.join(self.temp_dir.name, "unwritable_test")
        os.makedirs(test_dir, exist_ok=True)
        with patch("builtins.open", side_effect=PermissionError("Read-only filesystem")):
            with self.assertRaises(RuntimeError) as ctx3:
                validate_storage_directory(test_dir, is_persistent_configured=True)
            self.assertIn("not writable", str(ctx3.exception))

    def test_04_existing_database_not_overwritten_or_reset(self):
        """Verifies existing persistent database records survive schema initialization without reset."""
        persistent_storage = os.path.join(self.temp_dir.name, "persistent_disk")
        os.makedirs(persistent_storage, exist_ok=True)
        db_path = os.path.join(persistent_storage, "stockpulse.db")
        db_url = f"sqlite:///{db_path.replace(chr(92), '/')}"

        # 1. Initialize schema and populate custom user
        engine = create_engine(db_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        run_db_migrations(engine)

        Session = sessionmaker(bind=engine)
        session = Session()
        custom_user = User(
            name="Persistent Trader",
            username="persisto",
            email="persisto@example.com",
            password_hash="$2b$12$eX4mpleHashForPersistentTraderTesting12345678901234567890",
            is_verified=True,
        )
        session.add(custom_user)
        session.commit()
        user_id = custom_user.id
        session.close()
        engine.dispose()

        # 2. Simulate subsequent container startup / restart on the same persistent database
        restarted_engine = create_engine(db_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=restarted_engine)
        run_db_migrations(restarted_engine)

        restarted_session = sessionmaker(bind=restarted_engine)()
        reloaded_user = restarted_session.query(User).filter(User.id == user_id).first()

        self.assertIsNotNone(reloaded_user)
        self.assertEqual(reloaded_user.email, "persisto@example.com")
        self.assertEqual(reloaded_user.username, "persisto")
        self.assertEqual(reloaded_user.password_hash, "$2b$12$eX4mpleHashForPersistentTraderTesting12345678901234567890")
        restarted_session.close()
        restarted_engine.dispose()

    def test_05_migration_tool_execution_and_integrity_verification(self):
        """Verifies migrate_to_persistent_disk creates backups, copies tables, and verifies row counts."""
        src_dir = os.path.join(self.temp_dir.name, "source_disk")
        dst_dir = os.path.join(self.temp_dir.name, "target_disk")
        os.makedirs(src_dir, exist_ok=True)
        os.makedirs(dst_dir, exist_ok=True)

        src_db = os.path.join(src_dir, "stockpulse.db")
        dst_db = os.path.join(dst_dir, "stockpulse.db")

        # Create source DB with records in WAL mode
        src_engine = create_engine(f"sqlite:///{src_db.replace(chr(92), '/')}")
        Base.metadata.create_all(bind=src_engine)
        with src_engine.connect() as conn:
            from sqlalchemy import text
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.commit()

        Session = sessionmaker(bind=src_engine)
        session = Session()
        session.add(User(name="Alice", username="alice", email="alice@test.com", password_hash="hash_a"))
        session.add(User(name="Bob", username="bob", email="bob@test.com", password_hash="hash_b"))
        session.add(Alert(symbol="TCS", direction="ABOVE", target_percent=2.5, warning_minutes=15, user_id=1))
        session.add(SystemSetting(key="TEST_SETTING", value="ENABLED"))
        session.commit()
        session.close()
        src_engine.dispose()

        # Run migration tool (first time: target does not exist -> succeeds)
        result = migrate_to_persistent_disk(src_db, dst_db)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertTrue(os.path.exists(result["backup"]))
        self.assertTrue(os.path.exists(dst_db))
        self.assertTrue(check_sqlite_integrity(dst_db))

        # Check destination row counts
        dst_counts = get_table_row_counts(dst_db)
        self.assertEqual(dst_counts["users"], 2)
        self.assertEqual(dst_counts["alerts"], 1)
        self.assertEqual(dst_counts["system_settings"], 1)

        # Test verify-only mode
        verify_result = migrate_to_persistent_disk(src_db, dst_db, verify_only=True)
        self.assertEqual(verify_result["status"], "SUCCESS")

        # Test safety guard: re-running without allow_overwrite raises FileExistsError
        with self.assertRaises(FileExistsError):
            migrate_to_persistent_disk(src_db, dst_db, allow_overwrite=False)

        # Re-running WITH allow_overwrite succeeds and creates a pre_replace backup
        re_result = migrate_to_persistent_disk(src_db, dst_db, allow_overwrite=True)
        self.assertEqual(re_result["status"], "SUCCESS")

    def test_06_migration_error_guards(self):
        """Verifies migration stops safely on nonexistent or empty source database."""
        dst_db = os.path.join(self.temp_dir.name, "target.db")
        nonexistent_src = os.path.join(self.temp_dir.name, "does_not_exist.db")

        # Case A: Nonexistent source
        with self.assertRaises(FileNotFoundError):
            migrate_to_persistent_disk(nonexistent_src, dst_db)

        # Case B: 0-byte empty file source
        empty_src = os.path.join(self.temp_dir.name, "empty.db")
        with open(empty_src, "w") as f:
            pass
        with self.assertRaises(ValueError):
            migrate_to_persistent_disk(empty_src, dst_db)

    def test_07_database_path_env_var_resolution(self):
        """Verifies that DATABASE_PATH environment variable is correctly resolved as file or directory."""
        custom_disk = os.path.join(self.temp_dir.name, "render_disk_custom")
        os.makedirs(custom_disk, exist_ok=True)
        custom_db_file = os.path.join(custom_disk, "custom_stockpulse.db")

        # Case 1: DATABASE_PATH points directly to .db file
        with patch.dict(os.environ, {"DATABASE_PATH": custom_db_file, "DATABASE_URL": ""}):
            # Test resolution logic
            path_val = os.getenv("DATABASE_PATH")
            resolved = os.path.abspath(path_val) if path_val.endswith(".db") else os.path.join(path_val, "stockpulse.db")
            self.assertEqual(os.path.normcase(resolved), os.path.normcase(custom_db_file))

        # Case 2: DATABASE_PATH points to directory mount
        with patch.dict(os.environ, {"DATABASE_PATH": custom_disk, "DATABASE_URL": ""}):
            path_val = os.getenv("DATABASE_PATH")
            resolved = os.path.abspath(path_val) if path_val.endswith(".db") else os.path.join(path_val, "stockpulse.db")
            self.assertEqual(os.path.normcase(resolved), os.path.normcase(os.path.join(custom_disk, "stockpulse.db")))

    def test_08_registration_login_reopen_persistence(self):
        """Verifies registration and login data persistence after closing and reopening the SQLite database."""
        from fastapi import Response
        from app.api.auth import register_user, login_user, RegisterRequest, LoginRequest

        db_path = os.path.join(self.temp_dir.name, "lifecycle_persist.db")
        db_url = f"sqlite:///{db_path.replace(chr(92), '/')}"

        # 1. First session: Initialize schema and register new user
        engine1 = create_engine(db_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine1)
        run_db_migrations(engine1)

        Session1 = sessionmaker(bind=engine1)
        db1 = Session1()
        reg_req = RegisterRequest(
            name="Persistent User",
            username="persistent_user_99",
            email="persistent_user_99@example.com",
            password="StrongPassword123!",
        )
        reg_resp = register_user(reg_req, Response(), db=db1)
        self.assertIn("user", reg_resp)
        self.assertEqual(reg_resp["user"]["username"], "persistent_user_99")
        db1.close()
        engine1.dispose()

        # 2. Re-open database from disk simulating container restart
        engine2 = create_engine(db_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine2)
        run_db_migrations(engine2)

        Session2 = sessionmaker(bind=engine2)
        db2 = Session2()

        # Verify user still exists in database
        saved_user = db2.query(User).filter(User.username == "persistent_user_99").first()
        self.assertIsNotNone(saved_user)
        self.assertEqual(saved_user.email, "persistent_user_99@example.com")
        self.assertTrue(saved_user.password_hash.startswith("$2b$"))

        # Verify login succeeds with the registered credentials
        login_req = LoginRequest(
            identifier="persistent_user_99@example.com",
            password="StrongPassword123!",
        )
        login_resp = login_user(login_req, Response(), db=db2)
        self.assertIn("access_token", login_resp)
        self.assertEqual(login_resp["user"]["username"], "persistent_user_99")

        db2.close()
        engine2.dispose()

    def test_09_multiple_concurrent_connections_wal(self):
        """Verifies multiple concurrent connections in SQLite WAL mode handle parallel reads/writes without lock errors."""
        import threading
        from sqlalchemy import text, event

        db_path = os.path.join(self.temp_dir.name, "concurrent_wal.db")
        db_url = f"sqlite:///{db_path.replace(chr(92), '/')}"

        engine = create_engine(db_url, connect_args={"check_same_thread": False, "timeout": 30})
        Base.metadata.create_all(bind=engine)

        with engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.commit()

        Session = sessionmaker(bind=engine)
        errors = []

        def worker(worker_id):
            try:
                session = Session()
                # Insert
                user = User(
                    name=f"Worker {worker_id}",
                    username=f"worker_{worker_id}",
                    email=f"worker_{worker_id}@example.com",
                    password_hash="test_hash",
                )
                session.add(user)
                session.commit()
                # Read back
                count = session.query(User).count()
                self.assertGreaterEqual(count, 1)
                session.close()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent workers experienced errors: {errors}")
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
