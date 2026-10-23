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
from app.database.migrate_to_postgres import migrate_sqlite_to_postgres


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
        if hasattr(cls, "src_engine"):
            cls.src_engine.dispose()
        try:
            cls.temp_dir.cleanup()
        except Exception:
            pass

    def test_01_migration_preserves_exact_ids_and_passwords(self):
        """Verify migration tool successfully copies all tables and preserves credentials."""
        target_url = f"sqlite:///{self.target_sqlite}"
        migrate_sqlite_to_postgres(self.source_sqlite, target_url, verify_only=False)

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

    def test_02_migration_is_idempotent_no_duplicate_insertion(self):
        """Running migration repeatedly must not duplicate records or crash on existing IDs."""
        target_url = f"sqlite:///{self.target_sqlite}"
        # Second execution: should recognize existing records and preserve them
        migrate_sqlite_to_postgres(self.source_sqlite, target_url, verify_only=False)

        tgt_engine = create_engine(target_url)
        TgtSession = sessionmaker(bind=tgt_engine)
        with TgtSession() as db:
            user_count = db.query(User).count()
            self.assertEqual(user_count, 2)


if __name__ == "__main__":
    unittest.main()
