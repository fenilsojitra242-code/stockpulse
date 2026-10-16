import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from sqlalchemy import event

# Default to local SQLite database in data/ directory, with support for PostgreSQL
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/stockpulse.db")

# For SQLite, check_same_thread=False allows multi-threaded access across FastAPI and APScheduler
connect_args = {"check_same_thread": False, "timeout": 15} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=False
)

if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
        cursor.execute("PRAGMA temp_store=MEMORY")
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
                ("enable_supertrend", "BOOLEAN DEFAULT 1"),
                ("supertrend_proximity_percent", "FLOAT DEFAULT 0.50"),
                ("supertrend_proximity_points", "FLOAT DEFAULT 1.0"),
                ("last_supertrend_direction", "VARCHAR(10)"),
                ("last_supertrend_price", "FLOAT"),
                ("supertrend_approaching_sent", "BOOLEAN DEFAULT 0"),
                ("user_id", "INTEGER"),
            ],
            "users": [
                ("username", "VARCHAR(50)"),
                ("password_hash", "VARCHAR(255)"),
                ("is_verified", "BOOLEAN DEFAULT 1"),
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

            # Backfill any missing usernames from email prefix
            if "users" in table_names:
                conn.execute(text("UPDATE users SET username = lower(substr(email, 1, instr(email, '@') - 1)) WHERE username IS NULL OR username = ''"))
                conn.commit()
    except Exception as e:
        print(f"[DB Migration] Schema migration note: {e}")

