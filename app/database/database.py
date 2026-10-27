import os
import re
from typing import Optional
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from sqlalchemy import event

def validate_storage_directory(dir_path: str, is_persistent_configured: bool = False) -> str:
    """
    Validates that the database directory exists or can be created, and is writable.
    If is_persistent_configured is True, raises a clear RuntimeError on failure to prevent
    silently falling back to an ephemeral or invalid storage location.
    """
    abs_dir = os.path.abspath(dir_path)
    if os.path.exists(abs_dir):
        if not os.path.isdir(abs_dir):
            raise RuntimeError(f"Configured persistent storage path is not a directory: {abs_dir}")
        probe_file = os.path.join(abs_dir, f".stockpulse_probe_{os.getpid()}")
        try:
            with open(probe_file, "w") as f:
                f.write("probe")
            os.remove(probe_file)
        except Exception as e:
            raise RuntimeError(f"Configured persistent storage directory is not writable: {abs_dir} ({type(e).__name__})")
    else:
        try:
            os.makedirs(abs_dir, exist_ok=True)
        except Exception as e:
            if is_persistent_configured:
                raise RuntimeError(f"Failed to create configured persistent storage directory: {abs_dir} ({type(e).__name__})")
            raise
    return abs_dir


# Establish consistent, absolute database path based on project root or persistent storage
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Resolve DATABASE_URL / DATABASE_PATH: Support Neon PostgreSQL, Render Persistent Disk (/var/data), and local SQLite
raw_db_url = os.getenv("DATABASE_URL")
explicit_db_path = os.getenv("DATABASE_PATH") or os.getenv("SQLITE_DATABASE_PATH")
persistent_dir = (
    os.getenv("PERSISTENT_DATA_DIR")
    or os.getenv("PERSISTENT_STORAGE_DIR")
    or os.getenv("STOCKPULSE_DATA_DIR")
    or os.getenv("DATA_DIR")
)

if raw_db_url and (raw_db_url.startswith("postgres://") or raw_db_url.startswith("postgresql://") or raw_db_url.startswith("postgresql+")):
    # SQLAlchemy 2.0 requires postgresql://
    if raw_db_url.startswith("postgres://"):
        DATABASE_URL = raw_db_url.replace("postgres://", "postgresql://", 1)
    else:
        DATABASE_URL = raw_db_url
    DEFAULT_DB_FILE = None
    is_sqlite = False
    is_persistent_configured = False
else:
    is_sqlite = True
    if explicit_db_path:
        is_persistent_configured = True
        if explicit_db_path.endswith(".db") or explicit_db_path.endswith(".sqlite"):
            DEFAULT_DB_FILE = os.path.abspath(explicit_db_path) if os.path.isabs(explicit_db_path) else os.path.abspath(os.path.join(BASE_DIR, explicit_db_path))
        else:
            DEFAULT_DB_FILE = os.path.abspath(os.path.join(explicit_db_path, "stockpulse.db")) if os.path.isabs(explicit_db_path) else os.path.abspath(os.path.join(BASE_DIR, explicit_db_path, "stockpulse.db"))
    elif persistent_dir:
        is_persistent_configured = True
        if os.path.isabs(persistent_dir):
            DEFAULT_DB_FILE = os.path.join(persistent_dir, "stockpulse.db")
        else:
            DEFAULT_DB_FILE = os.path.abspath(os.path.join(BASE_DIR, persistent_dir, "stockpulse.db"))
    elif os.path.exists("/var/data") and os.path.isdir("/var/data"):
        # Auto-detect Render Persistent Disk mounted at /var/data if present
        DEFAULT_DB_FILE = "/var/data/stockpulse.db"
        is_persistent_configured = True
    else:
        DEFAULT_DB_FILE = os.path.join(BASE_DIR, "data", "stockpulse.db")
        is_persistent_configured = False

    if explicit_db_path:
        validate_storage_directory(os.path.dirname(DEFAULT_DB_FILE), is_persistent_configured=is_persistent_configured)
        DATABASE_URL = f"sqlite:///{DEFAULT_DB_FILE.replace(chr(92), '/')}"
    elif not raw_db_url:
        validate_storage_directory(os.path.dirname(DEFAULT_DB_FILE), is_persistent_configured=is_persistent_configured)
        DATABASE_URL = f"sqlite:///{DEFAULT_DB_FILE.replace(chr(92), '/')}"
    elif raw_db_url.startswith("sqlite:///"):
        path_part = raw_db_url[10:]
        if re.match(r'^[a-zA-Z]:[/\\].*', path_part):
            abs_db_file = os.path.abspath(path_part)
        elif path_part.startswith("/"):
            abs_db_file = os.path.abspath(path_part)
        else:
            rel_path = path_part.replace("./", "")
            abs_db_file = os.path.abspath(os.path.join(BASE_DIR, rel_path))
        validate_storage_directory(os.path.dirname(abs_db_file), is_persistent_configured=is_persistent_configured)
        DATABASE_URL = f"sqlite:///{abs_db_file.replace(chr(92), '/')}"
    else:
        DATABASE_URL = raw_db_url


def get_database_file_path() -> Optional[str]:
    """Returns canonical absolute filesystem path if using SQLite, else None."""
    if DATABASE_URL.startswith("sqlite:///"):
        path_part = DATABASE_URL[10:].split("?")[0]
        return os.path.abspath(path_part)
    if DEFAULT_DB_FILE:
        return os.path.abspath(DEFAULT_DB_FILE)
    return None


if is_sqlite:
    sqlite_target_file = get_database_file_path()
    if sqlite_target_file:
        validate_storage_directory(os.path.dirname(sqlite_target_file), is_persistent_configured=is_persistent_configured)
        print(f"[StockPulse] SQLite Database connected at: {sqlite_target_file}")
else:
    # Log database scheme with credentials securely masked
    masked_url = re.sub(r'://([^:@]+):([^@]+)@', r'://\1:***@', DATABASE_URL)
    print(f"[StockPulse] Database connected with URL: {masked_url}")

# For SQLite, check_same_thread=False allows multi-threaded access across FastAPI and APScheduler
# For PostgreSQL / Neon Free, enable connection pooling with pre-ping to handle cold starts & 5-minute auto-suspend
if is_sqlite:
    connect_args = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(
        DATABASE_URL,
        connect_args=connect_args,
        echo=False
    )
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,  # Reconnect automatically when Neon wakes up from auto-suspend
        pool_recycle=240,    # Discard idle connections before Neon's 5-minute timeout
        pool_size=5,         # Lightweight connection footprint for Render Free 512MB RAM
        max_overflow=10,
        pool_timeout=30,
        echo=False
    )

if is_sqlite:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.execute("PRAGMA busy_timeout=15000")  # 15s lock wait
        cursor.execute("PRAGMA mmap_size=268435456")  # 256MB memory mapped I/O
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


Base = declarative_base()


def get_db():
    """Dependency for FastAPI route handlers."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_db_migrations(db_engine):
    """Safely adds newly introduced columns to existing SQLite tables."""
    from sqlalchemy import inspect, text
    try:
        inspector = inspect(db_engine)
        table_names = inspector.get_table_names()

        table_migrations = {
            "alerts": [
                ("enable_supertrend", "BOOLEAN DEFAULT TRUE"),
                ("supertrend_proximity_percent", "FLOAT DEFAULT 0.50"),
                ("supertrend_proximity_points", "FLOAT DEFAULT 1.0"),
                ("last_supertrend_direction", "VARCHAR(10)"),
                ("last_supertrend_price", "FLOAT"),
                ("supertrend_approaching_sent", "BOOLEAN DEFAULT FALSE"),
                ("user_id", "INTEGER"),
            ],
            "users": [
                ("username", "VARCHAR(50)"),
                ("password_hash", "VARCHAR(255)"),
                ("is_verified", "BOOLEAN DEFAULT TRUE"),
            ],
            "notifications": [
                ("user_id", "INTEGER"),
                ("event_key", "VARCHAR(190)"),
            ],
            "alert_history": [
                ("user_id", "INTEGER"),
            ]
        }

        with db_engine.connect() as conn:
            for tbl, new_columns in table_migrations.items():
                if tbl in table_names:
                    existing_cols = {col["name"] for col in inspector.get_columns(tbl)}
                    for col_name, col_type in new_columns:
                        if col_name not in existing_cols:
                            conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN {col_name} {col_type}"))
                            conn.commit()
                            print(f"[DB Migration] Added missing column {tbl}.{col_name}")

            # Ensure unique index on event_key in notifications safely ignoring NULLs
            if "notifications" in table_names:
                try:
                    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_notifications_event_key ON notifications (event_key) WHERE event_key IS NOT NULL"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notifications_user_created ON notifications (user_id, created_at DESC)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notifications_user_read ON notifications (user_id, is_read)"))
                    conn.commit()
                except Exception as idx_err:
                    print(f"[DB Migration] Notification index migration note: {idx_err}")

            if "alerts" in table_names:
                try:
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_alerts_user_status ON alerts (user_id, status)"))
                    conn.commit()
                except Exception as idx_err:
                    print(f"[DB Migration] Alerts index migration note: {idx_err}")

            if "alert_history" in table_names:
                try:
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_alert_history_user_triggered ON alert_history (user_id, triggered_at DESC)"))
                    conn.commit()
                except Exception as idx_err:
                    print(f"[DB Migration] History index migration note: {idx_err}")

            # Backfill any missing usernames from email prefix and ensure unique indexes
            if "users" in table_names:
                try:
                    if db_engine.dialect.name == "postgresql":
                        conn.execute(text("UPDATE users SET username = lower(split_part(email, '@', 1)) WHERE (username IS NULL OR username = '') AND position('@' in email) > 1"))
                    else:
                        conn.execute(text("UPDATE users SET username = lower(substr(email, 1, instr(email, '@') - 1)) WHERE (username IS NULL OR username = '') AND instr(email, '@') > 1"))
                    conn.commit()
                except Exception as update_err:
                    print(f"[DB Migration] User username backfill note: {update_err}")

                try:
                    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username_lower ON users (lower(username)) WHERE username IS NOT NULL"))
                    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email_lower ON users (lower(email)) WHERE email IS NOT NULL"))
                    conn.commit()
                except Exception as user_idx_err:
                    print(f"[DB Migration] User index migration note: {user_idx_err}")
    except Exception as e:
        print(f"[DB Migration] Schema migration note: {e}")


def checkpoint_db(db_engine=None):
    """
    Safely checkpoints SQLite WAL log back into the main database file.
    Flushes all uncheckpointed WAL frames into stockpulse.db on shutdown or idle.
    """
    from sqlalchemy import text
    try:
        target = db_engine or engine
        if target.dialect.name == "sqlite":
            with target.connect() as conn:
                conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
                conn.commit()
    except Exception as e:
        print(f"[StockPulse] WAL checkpoint note: {e}")

