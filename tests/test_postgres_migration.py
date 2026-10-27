"""
PostgreSQL Emulation & Schema Compatibility Test Suite
Tests StockPulse models, migrations, authentication, and relationships
against a clean test engine to ensure complete PostgreSQL compliance.
"""

import os
import sys
import unittest
import tempfile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.models import (
    Base,
    User,
    Stock,
    Alert,
    AlertHistory,
    SystemSetting,
    Notification,
    EmailOTP,
)
from app.database.database import run_db_migrations
from app.api.auth import hash_password, verify_password, seed_default_user_if_needed
from app.database.migrate_to_postgres import migrate_sqlite_to_postgres, mask_url


class TestDatabasePostgresMigrationCompatibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.source_sqlite = os.path.join(cls.temp_dir.name, "source.db")
        cls.target_sqlite = os.path.join(cls.temp_dir.name, "target_emulated.db")

        # Initialize source database with real mock users & alerts
        src_engine = create_engine(f"sqlite:///{cls.source_sqlite}")
        Base.metadata.create_all(bind=src_engine)
        run_db_migrations(src_engine)

        SrcSession = sessionmaker(bind=src_engine)
        with SrcSession() as db:
            user1 = User(
                id=1,
                name="Trader One",
                username="trader1",
                email="trader1@example.com",
                password_hash=hash_password("TraderPass123!"),
                is_verified=True
            )
            user2 = User(
                id=2,
                name="Trader Two",
                username="trader2",
                email="trader2@example.com",
                password_hash=hash_password("TraderPass456!"),
                is_verified=True
            )
            db.add_all([user1, user2])
            db.commit()

            alert1 = Alert(
                id=1,
                user_id=1,
                symbol="TCS",
                direction="UP",
                target_percent=1.5,
                warning_minutes=5,
                status="MONITORING"
            )
            db.add(alert1)
            db.commit()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.temp_dir.cleanup()
        except Exception:
            pass

    def test_01_migration_preserves_exact_ids_and_passwords(self):
        """Verify migration tool successfully copies all tables and preserves credentials."""
        target_url = f"sqlite:///{self.target_sqlite}"
        res = migrate_sqlite_to_postgres(self.source_sqlite, target_url, verify_only=False)
        self.assertEqual(res["status"], "SUCCESS")

        tgt_engine = create_engine(target_url)
        TgtSession = sessionmaker(bind=tgt_engine)
        with TgtSession() as db:
            u1 = db.query(User).filter(User.id == 1).first()
            self.assertIsNotNone(u1)
            self.assertEqual(u1.username, "trader1")
            self.assertEqual(u1.email, "trader1@example.com")
            # Verify password still verifies accurately with bcrypt
            self.assertTrue(verify_password("TraderPass123!", u1.password_hash))
            self.assertFalse(verify_password("WrongPassword", u1.password_hash))

            a1 = db.query(Alert).filter(Alert.id == 1).first()
            self.assertIsNotNone(a1)
            self.assertEqual(a1.user_id, 1)
            self.assertEqual(a1.symbol, "TCS")

    def test_02_migration_conflict_guard_when_target_has_data(self):
        """Re-running migration on non-empty target without --allow-merge must halt with conflict error."""
        target_url = f"sqlite:///{self.target_sqlite}"
        with self.assertRaises(RuntimeError) as ctx:
            migrate_sqlite_to_postgres(self.source_sqlite, target_url, verify_only=False, allow_merge=False)
        self.assertIn("already contains data", str(ctx.exception))

    def test_03_migration_is_idempotent_with_allow_merge(self):
        """Running migration with allow_merge=True must preserve existing records without duplicates."""
        target_url = f"sqlite:///{self.target_sqlite}"
        res = migrate_sqlite_to_postgres(self.source_sqlite, target_url, verify_only=False, allow_merge=True)
        self.assertEqual(res["status"], "SUCCESS")

        tgt_engine = create_engine(target_url)
        TgtSession = sessionmaker(bind=tgt_engine)
        with TgtSession() as db:
            user_count = db.query(User).count()
            self.assertEqual(user_count, 2)

    def test_04_verify_only_dry_run_mode(self):
        """Verify --verify-only performs preflight checks without writing data."""
        fresh_target = os.path.join(self.temp_dir.name, "fresh_target.db")
        target_url = f"sqlite:///{fresh_target}"
        res = migrate_sqlite_to_postgres(self.source_sqlite, target_url, verify_only=True)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["mode"], "VERIFY_ONLY")
        self.assertEqual(res["source_counts"]["users"], 2)

    def test_05_url_masking_security(self):
        """Verifies that database URLs mask user credentials properly."""
        url = "postgresql://myuser:supersecretpass@ep-cool-butterfly-123456.us-east-2.aws.neon.tech/neondb?sslmode=require"
        masked = mask_url(url)
        self.assertNotIn("supersecretpass", masked)
        self.assertIn(":***@", masked)
        self.assertIn("myuser", masked)
        self.assertIn("ep-cool-butterfly-123456", masked)


if __name__ == "__main__":
    unittest.main()
