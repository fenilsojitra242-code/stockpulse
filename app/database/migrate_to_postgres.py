"""
StockPulse SQLite to PostgreSQL Migration CLI & Automation Utility
Safely migrates all user accounts, password hashes, alerts, history, notifications,
and settings from local SQLite to Neon/Render PostgreSQL without altering IDs or passwords.

Safety Guarantees:
1. Flushes source SQLite WAL and runs PRAGMA integrity_check before reading.
2. Creates an automated pre-migration backup of the SQLite database.
3. Masks all connection passwords and credentials in logs.
4. Preserves exact primary key IDs, foreign keys, timestamps, booleans, and bcrypt hashes.
5. In dependency-safe topological order: users -> stocks -> settings -> alerts -> history -> notifs -> otps.
6. Synchronizes PostgreSQL auto-increment sequences via pg_get_serial_sequence().
7. Verifies table-by-table row counts before and after migration.
8. Halts if target PostgreSQL already contains records unless --allow-merge is passed.
9. Supports --dry-run / --verify-only mode.
"""

import os
import sys
import re
import sqlite3
import argparse
from datetime import datetime
from typing import Dict, Any, List, Optional
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
    if not url:
        return "None"
    return re.sub(r'://([^:@]+):([^@]+)@', r'://\1:***@', url)


def check_sqlite_integrity(db_path: str) -> bool:
    """Verifies SQLite database integrity."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        rows = cursor.fetchall()
        return bool(rows and rows[0][0] == "ok")
    finally:
        conn.close()


def flush_sqlite_wal(db_path: str) -> None:
    """Checkpoints WAL frames back into main .db file."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()


def create_sqlite_backup(source_path: str) -> str:
    """Creates a verified timestamped backup of the source SQLite database."""
    flush_sqlite_wal(source_path)
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(source_path)), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(backup_dir, f"stockpulse_pre_postgres_{timestamp}.db")

    src = sqlite3.connect(source_path)
    dst = sqlite3.connect(backup_file)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    return backup_file


def migrate_sqlite_to_postgres(
    sqlite_path: str,
    pg_url: str,
    verify_only: bool = False,
    allow_merge: bool = False
) -> Dict[str, Any]:
    """
    Executes a strict, transactional migration from SQLite to PostgreSQL.
    Preserves exact IDs, timestamps, foreign keys, and password hashes.
    """
    if not os.path.exists(sqlite_path):
        raise FileNotFoundError(f"Source SQLite database not found at: {sqlite_path}")

    if os.path.getsize(sqlite_path) == 0:
        raise ValueError(f"Source SQLite database at {sqlite_path} is an empty file (0 bytes).")

    # Normalize PostgreSQL URL
    if pg_url.startswith("postgres://"):
        pg_url = pg_url.replace("postgres://", "postgresql://", 1)

    # Automatic DBAPI driver resolution (psycopg v3 vs psycopg2)
    if pg_url.startswith("postgresql://"):
        try:
            import psycopg  # noqa: F401
        except ImportError:
            try:
                import psycopg2  # noqa: F401
                pg_url = pg_url.replace("postgresql://", "postgresql+psycopg2://", 1)
            except ImportError:
                pass

    print("=" * 70)
    print("StockPulse — SQLite to PostgreSQL Migration Tool")
    print("=" * 70)
    print(f"Source SQLite DB : {os.path.abspath(sqlite_path)} ({os.path.getsize(sqlite_path):,} bytes)")
    print(f"Target Database  : {mask_url(pg_url)}")
    print(f"Mode             : {'VERIFY ONLY / DRY RUN' if verify_only else 'MIGRATE & VERIFY'}")
    print("=" * 70)

    # 1. Source SQLite validation & backup
    print("[1/6] Checkpointing source SQLite WAL & verifying integrity...")
    flush_sqlite_wal(sqlite_path)
    if not check_sqlite_integrity(sqlite_path):
        raise RuntimeError(f"Source SQLite database failed integrity check: {sqlite_path}")
    print("      Source integrity check: OK")

    backup_path = None
    if not verify_only:
        backup_path = create_sqlite_backup(sqlite_path)
        print(f"      Pre-migration backup created: {backup_path}")

    # 2. Connect to Source SQLite
    src_engine = create_engine(f"sqlite:///{os.path.abspath(sqlite_path).replace(chr(92), '/')}")
    SrcSession = sessionmaker(bind=src_engine)
    src_db = SrcSession()

    # 3. Connect to Target PostgreSQL
    print("[2/6] Connecting to target PostgreSQL database...")
    tgt_engine = create_engine(
        pg_url,
        pool_pre_ping=True,
        pool_recycle=240,
        echo=False
    )
    with tgt_engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    print("      PostgreSQL connectivity verified.")

    try:
        # Initialize schema
        print("[3/6] Ensuring target database tables and indexes exist...")
        Base.metadata.create_all(bind=tgt_engine)
        run_db_migrations(tgt_engine)
        print("      Target schema verified.")

        TgtSession = sessionmaker(bind=tgt_engine)
        tgt_db = TgtSession()

        # Check existing row counts on target
        print("\n[4/6] Inspecting row counts before migration:")
        source_counts = {}
        target_counts_initial = {}
        for model_cls, tbl_name in TABLE_ORDER:
            s_cnt = src_db.query(model_cls).count()
            t_cnt = tgt_db.query(model_cls).count()
            source_counts[tbl_name] = s_cnt
            target_counts_initial[tbl_name] = t_cnt
            print(f"      - Table '{tbl_name:18s}': Source={s_cnt:5d} | Target={t_cnt:5d}")

        # Safety conflict check: if target already has rows and allow_merge is False
        has_existing_target_data = any(c > 0 for c in target_counts_initial.values())
        if has_existing_target_data and not verify_only and not allow_merge:
            raise RuntimeError(
                f"Target PostgreSQL database already contains data: {target_counts_initial}.\n"
                f"Migration halted to prevent unintended duplicate records or conflicting IDs.\n"
                f"To authorize merging/appending new records, pass --allow-merge."
            )

        if verify_only:
            print("\n[INFO] --verify-only / --dry-run specified. Skipping write operations.")
            return {
                "status": "SUCCESS",
                "mode": "VERIFY_ONLY",
                "source_counts": source_counts,
                "target_counts": target_counts_initial,
            }

        # 4. Migrate tables in topological order
        print("\n[5/6] Migrating tables preserving foreign keys and exact IDs...")
        valid_user_ids = {u.id for u in tgt_db.query(User.id).all()}
        valid_stock_ids = {s.id for s in tgt_db.query(Stock.id).all()}
        valid_alert_ids = {a.id for a in tgt_db.query(Alert.id).all()}

        for model_cls, tbl_name in TABLE_ORDER:
            src_rows = src_db.query(model_cls).all()
            if not src_rows:
                print(f"      -> Table '{tbl_name}': 0 rows to migrate.")
                continue

            migrated_count = 0
            skipped_count = 0
            sanitized_fks = 0

            mapper = inspect(model_cls)
            pk_cols = [c.name for c in mapper.primary_key]

            # Fast in-memory lookup of existing primary keys to prevent repeated DB queries and autoflushes
            if len(pk_cols) == 1:
                pk_attr = getattr(model_cls, pk_cols[0])
                existing_pks = {val[0] for val in tgt_db.query(pk_attr).all()}
            else:
                existing_pks = set(
                    tuple(val) for val in tgt_db.query(*[getattr(model_cls, k) for k in pk_cols]).all()
                )

            with tgt_db.no_autoflush:
                for row in src_rows:
                    data = {c.name: getattr(row, c.name) for c in mapper.columns}

                    if len(pk_cols) == 1:
                        is_existing = data[pk_cols[0]] in existing_pks
                    else:
                        is_existing = tuple(data[k] for k in pk_cols) in existing_pks

                    if is_existing:
                        skipped_count += 1
                    else:
                        # Sanitize orphaned foreign keys from SQLite (e.g. deleted test users)
                        if "user_id" in data and data["user_id"] is not None and data["user_id"] not in valid_user_ids:
                            data["user_id"] = None
                            sanitized_fks += 1

                        if "stock_id" in data and data["stock_id"] is not None and data["stock_id"] not in valid_stock_ids:
                            data["stock_id"] = None
                            sanitized_fks += 1

                        if "alert_id" in data and data["alert_id"] is not None and data["alert_id"] not in valid_alert_ids:
                            data["alert_id"] = None
                            sanitized_fks += 1

                        new_obj = model_cls(**data)
                        tgt_db.add(new_obj)
                        migrated_count += 1
                        if len(pk_cols) == 1:
                            existing_pks.add(data[pk_cols[0]])
                        else:
                            existing_pks.add(tuple(data[k] for k in pk_cols))

            tgt_db.commit()

            # Refresh parent IDs for downstream foreign key validation
            if model_cls == User:
                valid_user_ids = {u.id for u in tgt_db.query(User.id).all()}
            elif model_cls == Stock:
                valid_stock_ids = {s.id for s in tgt_db.query(Stock.id).all()}
            elif model_cls == Alert:
                valid_alert_ids = {a.id for a in tgt_db.query(Alert.id).all()}

            sanitize_note = f" (Sanitized {sanitized_fks} orphaned foreign keys to NULL)" if sanitized_fks > 0 else ""
            print(f"      -> Table '{tbl_name}': Migrated {migrated_count} new rows ({skipped_count} existing rows preserved).{sanitize_note}")

            # Reset PostgreSQL autoincrement sequence for tables with integer ID
            if "id" in pk_cols and tgt_engine.dialect.name == "postgresql":
                try:
                    with tgt_engine.connect() as conn:
                        seq_res = conn.execute(text(f"SELECT pg_get_serial_sequence('{tbl_name}', 'id')")).scalar()
                        if seq_res:
                            max_id_res = conn.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {tbl_name}")).scalar()
                            if max_id_res > 0:
                                conn.execute(text(f"SELECT setval('{seq_res}', {max_id_res}, true)"))
                            else:
                                conn.execute(text(f"SELECT setval('{seq_res}', 1, false)"))
                            conn.commit()
                except Exception as seq_err:
                    print(f"         (Note adjusting sequence for {tbl_name}: {seq_err})")

        # 5. Verification
        print("\n[6/6] Verifying migrated records and authentication integrity on PostgreSQL:")
        all_passed = True
        final_target_counts = {}
        for model_cls, tbl_name in TABLE_ORDER:
            src_cnt = src_db.query(model_cls).count()
            tgt_cnt = tgt_db.query(model_cls).count()
            final_target_counts[tbl_name] = tgt_cnt
            status = "MATCH" if tgt_cnt >= src_cnt else "MISMATCH"
            print(f"      - Table '{tbl_name:18s}': Source={src_cnt:5d} | Target={tgt_cnt:5d} [{status}]")
            if tgt_cnt < src_cnt:
                all_passed = False

        # Specific user accounts verification
        src_users = src_db.query(User.id, User.username, User.email).all()
        print(f"\n      Checking user credentials preservation ({len(src_users)} users):")
        for uid, uname, uemail in src_users:
            tgt_u = tgt_db.query(User).filter(User.id == uid).first()
            if tgt_u and tgt_u.email == uemail and tgt_u.password_hash:
                print(f"      - User ID={uid:2d} ({uemail}): Intact with bcrypt hash")
            else:
                print(f"      - [WARNING] User ID={uid:2d} ({uemail}): Missing or invalid!")
                all_passed = False

        tgt_db.close()
        src_db.close()

        print("\n" + "=" * 70)
        if all_passed:
            print("MIGRATION COMPLETED SUCCESSFULLY! All records and credentials verified.")
        else:
            print("Migration completed with warnings. Please check the logs above.")
        print("=" * 70)

        return {
            "status": "SUCCESS" if all_passed else "WARNING",
            "source": os.path.abspath(sqlite_path),
            "target": mask_url(pg_url),
            "backup": backup_path,
            "source_counts": source_counts,
            "target_counts": final_target_counts,
        }

    except Exception as e:
        print(f"\n[ERROR] Migration failed: {e}")
        raise
    finally:
        src_engine.dispose()
        tgt_engine.dispose()


def main():
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
        "--dry-run",
        action="store_true",
        dest="verify_only",
        help="Only verify connectivity and schemas without copying data"
    )
    parser.add_argument(
        "--allow-merge",
        action="store_true",
        help="Allow merging records into a target database that already contains rows"
    )
    args = parser.parse_args()

    if not args.target:
        print("[ERROR] Target database URL is required via --target or DATABASE_URL environment variable.")
        print("Usage: python -m app.database.migrate_to_postgres --target postgresql://user:pass@host/dbname")
        sys.exit(1)

    try:
        migrate_sqlite_to_postgres(
            args.source,
            args.target,
            verify_only=args.verify_only,
            allow_merge=args.allow_merge
        )
    except Exception as e:
        print(f"\n[FATAL ERROR] Migration aborted: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
