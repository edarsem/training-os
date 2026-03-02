from __future__ import annotations

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine, run_sqlite_schema_updates
from app.core.training_load_defaults import (
    DEFAULT_TRAINING_LOAD_ATL_DAYS,
    DEFAULT_TRAINING_LOAD_CTL_DAYS,
    DEFAULT_TRAINING_LOAD_ZONE_COEFFICIENTS,
)
from app.crud import crud
from app.training_load import TrainingLoadConfig, compute_training_load_series


def recompute_all() -> None:
    if settings.TRAINING_LOAD_THRESHOLD_HR_BPM is None:
        raise RuntimeError("TRAINING_LOAD_THRESHOLD_HR_BPM is missing. Set it in .env.")

    Base.metadata.create_all(bind=engine)
    run_sqlite_schema_updates()

    db = SessionLocal()
    try:
        first_session_date = crud.get_first_session_date(db)
        last_session_date = crud.get_last_session_date(db)
        if not first_session_date or not last_session_date:
            print("No sessions found. Nothing to compute.")
            return

        sessions = crud.get_sessions_by_date_range(db, first_session_date, last_session_date)
        zone_map = crud.get_session_hr_zone_time_map(
            db,
            [int(session.id) for session in sessions if session.id is not None],
        )

        config = TrainingLoadConfig(
            threshold_hr=float(settings.TRAINING_LOAD_THRESHOLD_HR_BPM),
            zone_coefficients=list(DEFAULT_TRAINING_LOAD_ZONE_COEFFICIENTS),
            atl_time_constant_days=float(DEFAULT_TRAINING_LOAD_ATL_DAYS),
            ctl_time_constant_days=float(DEFAULT_TRAINING_LOAD_CTL_DAYS),
        )

        computed = compute_training_load_series(
            sessions=sessions,
            session_zone_time_map=zone_map,
            start_date=first_session_date,
            end_date=last_session_date,
            config=config,
        )

        session_load_map: dict[int, float] = {}
        for point in computed["daily"]:
            for breakdown in point.get("session_breakdown", []):
                session_id = breakdown.get("session_id")
                if session_id is None:
                    continue
                session_load = float(breakdown.get("session_load", 0.0))
                session_load_map[int(session_id)] = session_load_map.get(int(session_id), 0.0) + session_load

        for session in sessions:
            session.training_load = round(float(session_load_map.get(int(session.id), 0.0)), 3)

        crud.upsert_daily_training_load_points(db, computed["daily"])
        db.commit()

        print(
            "Recompute complete: sessions={sessions_count}, days={days_count}, "
            "current_atl={atl}, current_ctl={ctl}, current_acwr={acwr}".format(
                sessions_count=len(sessions),
                days_count=len(computed["daily"]),
                atl=computed.get("current_atl"),
                ctl=computed.get("current_ctl"),
                acwr=computed.get("current_acwr"),
            )
        )
    finally:
        db.close()


def main() -> None:
    recompute_all()


if __name__ == "__main__":
    main()
