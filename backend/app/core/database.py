from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import text
from app.core.config import settings

if settings.DATABASE_URL.startswith("sqlite:///"):
    sqlite_path = settings.DATABASE_URL.replace("sqlite:///", "", 1)
    from pathlib import Path
    Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def run_sqlite_schema_updates() -> None:
    if not settings.DATABASE_URL.startswith("sqlite:///"):
        return

    with engine.begin() as conn:
        table_info_rows = conn.execute(text("PRAGMA table_info(sessions)")).fetchall()
        session_columns = {str(row[1]) for row in table_info_rows}
        if "training_load" not in session_columns:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN training_load FLOAT"))
        if "timezone_name" not in session_columns:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN timezone_name VARCHAR"))
        if "training_load_elapsed" not in session_columns:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN training_load_elapsed FLOAT"))
        if "hr_stream_json" not in session_columns:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN hr_stream_json TEXT"))
        if "is_race" not in session_columns:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN is_race BOOLEAN NOT NULL DEFAULT 0"))
        conn.execute(text("UPDATE sessions SET is_race = 0 WHERE is_race IS NULL"))

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS daily_training_load (
                    date DATE PRIMARY KEY,
                    load FLOAT NOT NULL DEFAULT 0.0,
                    atl FLOAT NOT NULL DEFAULT 0.0,
                    ctl FLOAT NOT NULL DEFAULT 0.0,
                    acwr FLOAT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NULL
                )
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS session_hr_zone_time (
                    session_id INTEGER PRIMARY KEY,
                    zone_0_seconds INTEGER NOT NULL DEFAULT 0,
                    zone_1_seconds INTEGER NOT NULL DEFAULT 0,
                    zone_2_seconds INTEGER NOT NULL DEFAULT 0,
                    zone_3_seconds INTEGER NOT NULL DEFAULT 0,
                    zone_4_seconds INTEGER NOT NULL DEFAULT 0,
                    zone_5_seconds INTEGER NOT NULL DEFAULT 0,
                    zone_6_seconds INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                )
                """
            )
        )

        session_zone_rows = conn.execute(text("PRAGMA table_info(session_hr_zone_time)")).fetchall()
        session_zone_columns = {str(row[1]) for row in session_zone_rows}

        if "zone_0_seconds" not in session_zone_columns:
            conn.execute(text("ALTER TABLE session_hr_zone_time ADD COLUMN zone_0_seconds INTEGER NOT NULL DEFAULT 0"))

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
