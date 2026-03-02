from __future__ import annotations

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine, run_sqlite_schema_updates
from app.training_load_recompute import recompute_training_load_full_history


def recompute_all() -> None:
    if settings.TRAINING_LOAD_THRESHOLD_HR_BPM is None:
        raise RuntimeError("TRAINING_LOAD_THRESHOLD_HR_BPM is missing. Set it in .env.")

    Base.metadata.create_all(bind=engine)
    run_sqlite_schema_updates()

    db = SessionLocal()
    try:
        result = recompute_training_load_full_history(db)
        if not result.recomputed_from_date or not result.recomputed_to_date:
            print("No sessions found. Nothing to compute.")
            return

        print(
            "Recompute complete: sessions={sessions_count}, days={days_count}, "
            "current_atl={atl}, current_ctl={ctl}, current_acwr={acwr}".format(
                sessions_count=result.sessions_updated,
                days_count=result.days_recomputed,
                atl=result.current_atl,
                ctl=result.current_ctl,
                acwr=result.current_acwr,
            )
        )
    finally:
        db.close()


def main() -> None:
    recompute_all()


if __name__ == "__main__":
    main()
