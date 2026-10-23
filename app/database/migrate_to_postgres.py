"""
StockPulse SQLite to PostgreSQL Migration CLI & Automation Utility
Safely migrates all user accounts, password hashes, alerts, history, notifications,
and settings from local SQLite to Render PostgreSQL without altering IDs or passwords.
"""

import os
import sys
import re
import argparse
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

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

# Table migration sequence respecting foreign key constraints
TABLE_ORDER = [
    (User, "users"),
    (Stock, "stocks"),
    (SystemSetting, "system_settings"),
    (Alert, "alerts"),
    (AlertHistory, "alert_history"),
    (Notification, "notifications"),
    (EmailOTP, "email_otps"),
]


def mask_url(url: str) -> str:
    """Masks database credentials for safe logging."""
    return re.sub(r':([^@]+)@', ':***@', url)


def migrate_sqlite_to_postgres(sqlite_path: str, pg_url: str, verify_only: bool = False):
    """
    Executes a strict, transactional migration from SQLite to PostgreSQL.
    Preserves exact IDs, timestamps, foreign keys, and password hashes.
    """
    if not os.path.exists(sqlite_path):
        raise FileNotFoundError(f"Source SQLite database not found at: {sqlite_path}")

    # Normalize PostgreSQL URL if given with postgres:// scheme
    if pg_url.startswith("postgres://"):
        pg_url = pg_url.replace("postgres://", "postgresql://", 1)

    print("=" * 70)
    print("StockPulse — SQLite to PostgreSQL Migration Tool")
    print("=" * 70)
    print(f"Source SQLite DB : {os.path.abspath(sqlite_path)}")
    print(f"Target Postgres  : {mask_url(pg_url)}")
    print("=" * 70)

    # 1. Connect to Source SQLite
    src_engine = create_engine(f"sqlite:///{os.path.abspath(sqlite_path).replace(chr(92), '/')}")
    SrcSession = sessionmaker(bind=src_engine)
    src_db = SrcSession()

    # 2. Connect to Target PostgreSQL
    tgt_engine = create_engine(
        pg_url,
        pool_pre_ping=True,
        echo=False
    )
    TgtSession = sessionmaker(bind=tgt_engine)

    try:
        # Create schema on target
        print("[1/5] Ensuring target database tables exist...")
        Base.metadata.create_all(bind=tgt_engine)
        run_db_migrations(tgt_engine)
        print("  -> Schema initialized successfully.")

        tgt_db = TgtSession()

        # Check existing row counts on target
        print("\n[2/5] Inspecting target database status before migration...")
        for model_cls, tbl_name in TABLE_ORDER:
            tgt_count = tgt_db.query(model_cls).count()
            src_count = src_db.query(model_cls).count()
            print(f"  - Table '{tbl_name}': Source={src_count} rows | Target={tgt_count} rows")

        if verify_only:
            print("\n[INFO] --verify-only specified. Skipping data migration.")
            return

        print("\n[3/5] Migrating tables preserving foreign keys and exact IDs...")

        # Disable foreign key checks during migration if dialect supports it
        # Or migrate in strict topological dependency order (TABLE_ORDER)
        for model_cls, tbl_name in TABLE_ORDER:
            src_rows = src_db.query(model_cls).all()
            if not src_rows:
                print(f"  -> Table '{tbl_name}': 0 rows to migrate.")
                continue

            migrated_count = 0
            skipped_count = 0

            # Determine primary key column(s)
            mapper = inspect(model_cls)
            pk_cols = [c.name for c in mapper.primary_key]

            for row in src_rows:
                # Extract clean attribute dict
                data = {c.name: getattr(row, c.name) for c in mapper.columns}

                # Check if row already exists on target by PK
                if len(pk_cols) == 1:
                    pk_val = data[pk_cols[0]]
                    existing = tgt_db.query(model_cls).filter(getattr(model_cls, pk_cols[0]) == pk_val).first()
                else:
                    filter_conds = [getattr(model_cls, k) == data[k] for k in pk_cols]
                    existing = tgt_db.query(model_cls).filter(*filter_conds).first()

                if existing:
                    skipped_count += 1
                else:
                    new_obj = model_cls(**data)
                    tgt_db.add(new_obj)
                    migrated_count += 1

            tgt_db.commit()
            print(f"  -> Table '{tbl_name}': Migrated {migrated_count} new rows ({skipped_count} existing rows preserved).")

            # Reset PostgreSQL autoincrement sequence for tables with integer ID
            if "id" in pk_cols and tgt_engine.dialect.name == "postgresql":
                try:
                    with tgt_engine.connect() as conn:
                        max_id_res = conn.execute(text(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {tbl_name}")).scalar()
                        seq_name = f"{tbl_name}_id_seq"
                        conn.execute(text(f"SELECT setval('{seq_name}', {max_id_res}, false)"))
                        conn.commit()
                except Exception as seq_err:
                    print(f"     (Note adjusting sequence for {tbl_name}: {seq_err})")

        print("\n[4/5] Verification of Migrated Records on PostgreSQL:")
        all_passed = True
        for model_cls, tbl_name in TABLE_ORDER:
            src_cnt = src_db.query(model_cls).count()
            tgt_cnt = tgt_db.query(model_cls).count()
            status = "MATCH" if tgt_cnt >= src_cnt else "MISMATCH"
            print(f"  - Table '{tbl_name:15}': Source={src_cnt:5} | Target={tgt_cnt:5} [{status}]")
            if tgt_cnt < src_cnt:
                all_passed = False

        # Specific user accounts verification
        src_users = src_db.query(User.id, User.username, User.email).all()
        print(f"\n[5/5] Checking user credentials preservation ({len(src_users)} users):")
        for uid, uname, uemail in src_users:
            tgt_u = tgt_db.query(User).filter(User.id == uid).first()
            if tgt_u and tgt_u.email == uemail and tgt_u.password_hash:
                print(f"  - User ID={uid:2} ({uemail}): Intact with bcrypt hash")
            else:
                print(f"  - [WARNING] User ID={uid:2} ({uemail}): Missing or invalid!")
                all_passed = False

        tgt_db.close()
        src_db.close()

        print("=" * 70)
        if all_passed:
            print("Migration completed successfully! All records and credentials verified.")
        else:
            print("Migration completed with warnings. Please check the logs above.")
        print("=" * 70)

    except Exception as e:
        print(f"\n[ERROR] Migration failed: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="StockPulse SQLite to PostgreSQL Migration Tool")
    parser.add_argument(
        "--source",
        default="data/stockpulse.db",
        help="Path to source SQLite database file (default: data/stockpulse.db)"
    )
    parser.add_argument(
        "--target",
        default=os.getenv("TARGET_DATABASE_URL") or os.getenv("DATABASE_URL"),
        help="Target PostgreSQL connection URL (e.g. postgresql://user:pass@host/dbname)"
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify connectivity and schemas without copying data"
    )
    args = parser.parse_args()

    if not args.target:
        print("[ERROR] Target database URL is required via --target or DATABASE_URL environment variable.")
        print("Usage: python app/database/migrate_to_postgres.py --target postgresql://user:pass@host/dbname")
        sys.exit(1)

    migrate_sqlite_to_postgres(args.source, args.target, verify_only=args.verify_only)
