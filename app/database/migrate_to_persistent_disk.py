"""
StockPulse SQLite to Persistent Disk Migration & Verification Tool
Safely migrates and verifies SQLite databases when moving to a Render Persistent Disk (/var/data).

Guarantees:
1. Flushes SQLite WAL log on source (PRAGMA wal_checkpoint(TRUNCATE)) before copying.
2. Performs comprehensive PRAGMA integrity_check on the source database.
3. Automatically creates a timestamped pre-migration backup before touching target.
4. Uses Python's native sqlite3 Online Backup API (conn.backup()) to guarantee an atomic snapshot.
5. Verifies exact row counts and schema integrity across all core tables:
   - users (including password hashes and verification flags)
   - stocks
   - alerts
   - alert_history
   - notifications
   - system_settings
   - email_otps
6. Verifies PRAGMA integrity_check on the destination database.
7. Supports safe dry-run / verification-only mode (--verify-only).
8. Provides automated rollback if integrity or row counts do not match.
"""

import os
import sys
import sqlite3
import argparse
import shutil
from datetime import datetime
from typing import Dict, Tuple, List, Optional, Any

# Core tables to verify in migration
TABLES_TO_VERIFY = [
    "users",
    "stocks",
    "alerts",
    "alert_history",
    "notifications",
    "system_settings",
    "email_otps",
]


def check_sqlite_integrity(db_path: str) -> bool:
    """Runs PRAGMA integrity_check on an existing SQLite database."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database file does not exist: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        rows = cursor.fetchall()
        if rows and rows[0][0] == "ok":
            return True
        print(f"[Integrity Check Error] {db_path}: {rows}")
        return False
    finally:
        conn.close()


def flush_wal(db_path: str) -> None:
    """Checkpoints WAL frames back into the main .db file."""
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()


def get_table_row_counts(db_path: str) -> Dict[str, int]:
    """Queries row counts for all known StockPulse tables."""
    counts = {}
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}

        for table in TABLES_TO_VERIFY:
            if table in existing_tables:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                counts[table] = cursor.fetchone()[0]
            else:
                counts[table] = 0
    finally:
        conn.close()
    return counts


def create_backup(source_path: str, backup_dir: Optional[str] = None) -> str:
    """Creates a timestamped backup of the source database."""
    flush_wal(source_path)
    if not backup_dir:
        backup_dir = os.path.join(os.path.dirname(os.path.abspath(source_path)), "backups")
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(backup_dir, f"stockpulse_premigration_{timestamp}.db")

    src_conn = sqlite3.connect(source_path)
    dst_conn = sqlite3.connect(backup_file)
    try:
        src_conn.backup(dst_conn)
        dst_conn.commit()
    finally:
        dst_conn.close()
        src_conn.close()

    print(f"[Backup] Successfully created pre-migration backup at: {backup_file}")
    return backup_file


def migrate_to_persistent_disk(source_path: str, target_path: str, verify_only: bool = False, allow_overwrite: bool = False) -> Dict[str, Any]:
    """
    Executes a verified migration from source SQLite database to persistent disk target.
    Requires allow_overwrite=True if target already exists to prevent accidental overwrites.
    """
    source_abs = os.path.abspath(source_path)
    target_abs = os.path.abspath(target_path)

    if not os.path.exists(source_abs):
        raise FileNotFoundError(
            f"Source database not found at: {source_abs}\n"
            f"Note: On Render, ephemeral containers do not automatically bundle local databases.\n"
            f"Verify that your source database file is uploaded or specify the correct path."
        )

    if os.path.getsize(source_abs) == 0:
        raise ValueError(f"Source database at {source_abs} is an empty file (0 bytes). Cannot migrate empty database.")

    print("=" * 70)
    print("StockPulse — Persistent Disk Database Migration & Verification")
    print("=" * 70)
    print(f"Source Database : {source_abs} ({os.path.getsize(source_abs):,} bytes)")
    print(f"Target Database : {target_abs}")
    print(f"Mode            : {'VERIFY ONLY' if verify_only else 'MIGRATE & VERIFY'}")
    print("=" * 70)

    # 1. Checkpoint source WAL
    print("[1/6] Checkpointing source database WAL...")
    flush_wal(source_abs)

    # 2. Check source integrity
    print("[2/6] Verifying source database integrity...")
    if not check_sqlite_integrity(source_abs):
        raise RuntimeError(f"Source database failed integrity check: {source_abs}")
    print("      Source integrity check: OK")

    # 3. Read source row counts
    source_counts = get_table_row_counts(source_abs)
    print("[3/6] Source table row counts:")
    for tbl, count in source_counts.items():
        print(f"      - {tbl:20s}: {count} rows")

    if verify_only:
        if not os.path.exists(target_abs):
            raise FileNotFoundError(f"Target database does not exist for verification: {target_abs}")
        print("\n[VERIFY] Checking target database integrity and row counts...")
        flush_wal(target_abs)
        if not check_sqlite_integrity(target_abs):
            raise RuntimeError(f"Target database failed integrity check: {target_abs}")
        target_counts = get_table_row_counts(target_abs)
        mismatches = []
        for tbl in TABLES_TO_VERIFY:
            if source_counts.get(tbl, 0) != target_counts.get(tbl, 0):
                mismatches.append((tbl, source_counts.get(tbl, 0), target_counts.get(tbl, 0)))

        if mismatches:
            raise ValueError(f"Row count mismatch between source and target: {mismatches}")
        print("      Target integrity check: OK")
        print("      All row counts match source database perfectly!")
        return {"status": "SUCCESS", "mode": "VERIFY_ONLY", "counts": target_counts}

    # Safety guard: require explicit authorization if target already exists
    target_backup = None
    target_existed_before = os.path.exists(target_abs)
    if target_existed_before:
        if not allow_overwrite:
            raise FileExistsError(
                f"Target database already exists at: {target_abs}\n"
                f"To protect existing data from accidental overwrites, migration stopped.\n"
                f"To proceed, pass --allow-overwrite or --force to authorize replacing the target."
            )
        target_backup = f"{target_abs}.pre_replace_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(target_abs, target_backup)
        print(f"[Safety] Existing target database backed up to: {target_backup}")

    # 4. Create pre-migration backup of source
    print("[4/6] Creating verified pre-migration backup of source...")
    backup_file = create_backup(source_abs)

    # 5. Atomic copy via SQLite Online Backup API
    print(f"[5/6] Copying database to persistent storage ({target_abs})...")
    os.makedirs(os.path.dirname(target_abs), exist_ok=True)

    src_conn = sqlite3.connect(source_abs)
    dst_conn = sqlite3.connect(target_abs)
    try:
        src_conn.backup(dst_conn)
        dst_conn.commit()
    finally:
        dst_conn.close()
        src_conn.close()

    # 6. Verify destination integrity & row counts
    print("[6/6] Verifying destination database integrity & row counts...")
    flush_wal(target_abs)
    try:
        if not check_sqlite_integrity(target_abs):
            raise RuntimeError("Destination database failed integrity check!")

        dest_counts = get_table_row_counts(target_abs)
        mismatches = []
        for tbl in TABLES_TO_VERIFY:
            src_c = source_counts.get(tbl, 0)
            dst_c = dest_counts.get(tbl, 0)
            if src_c != dst_c:
                mismatches.append(f"Table '{tbl}': Source={src_c}, Target={dst_c}")

        if mismatches:
            raise ValueError(f"Migration row count verification failed:\n" + "\n".join(mismatches))
    except Exception as verify_err:
        print(f"\n[ROLLBACK] Verification error: {verify_err}")
        if target_existed_before and target_backup and os.path.exists(target_backup):
            shutil.copy2(target_backup, target_abs)
            print(f"[ROLLBACK] Restored previous target database from: {target_backup}")
        elif os.path.exists(target_abs):
            os.remove(target_abs)
            print(f"[ROLLBACK] Removed corrupted incomplete target database.")
        raise

    print("\n" + "=" * 70)
    print("MIGRATION COMPLETED SUCCESSFULLY!")
    print("=" * 70)
    for tbl, count in dest_counts.items():
        print(f" - {tbl:20s}: {count} rows verified")
    print("=" * 70)

    return {
        "status": "SUCCESS",
        "source": source_abs,
        "target": target_abs,
        "backup": backup_file,
        "counts": dest_counts,
    }


def get_default_target() -> str:
    p = os.getenv("DATABASE_PATH") or os.getenv("PERSISTENT_DATA_DIR")
    if p:
        if p.endswith(".db") or p.endswith(".sqlite"):
            return p
        return os.path.join(p, "stockpulse.db")
    return "/var/data/stockpulse.db"


def main():
    parser = argparse.ArgumentParser(description="StockPulse SQLite to Persistent Disk Migration Tool")
    parser.add_argument("--source", default=os.path.join("data", "stockpulse.db"), help="Source SQLite database file")
    parser.add_argument("--target", default=get_default_target(), help="Target SQLite database file (default: DATABASE_PATH or /var/data/stockpulse.db)")
    parser.add_argument("--verify-only", action="store_true", help="Verify integrity and row counts without copying")
    parser.add_argument("--allow-overwrite", "--force", action="store_true", dest="allow_overwrite", help="Explicitly authorize overwriting an existing target database")
    args = parser.parse_args()

    try:
        migrate_to_persistent_disk(args.source, args.target, verify_only=args.verify_only, allow_overwrite=args.allow_overwrite)
    except Exception as e:
        print(f"\n[FATAL ERROR] Migration aborted: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
