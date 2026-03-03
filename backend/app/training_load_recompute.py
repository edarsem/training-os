from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.core.training_load_defaults import (
    DEFAULT_TRAINING_LOAD_ATL_DAYS,
    DEFAULT_TRAINING_LOAD_CTL_DAYS,
)
from app.crud import crud
from app.models import models
from app.training_load import TrainingLoadConfig, compute_training_load_series


@dataclass
class TrainingLoadRecomputeResult:
    recomputed_from_date: date | None
    recomputed_to_date: date | None
    days_recomputed: int
    sessions_updated: int
    current_atl: float
    current_ctl: float
    current_acwr: float | None


def _build_training_load_config() -> TrainingLoadConfig:
    return TrainingLoadConfig(
        atl_time_constant_days=float(DEFAULT_TRAINING_LOAD_ATL_DAYS),
        ctl_time_constant_days=float(DEFAULT_TRAINING_LOAD_CTL_DAYS),
    )


def _build_empty_result() -> TrainingLoadRecomputeResult:
    return TrainingLoadRecomputeResult(
        recomputed_from_date=None,
        recomputed_to_date=None,
        days_recomputed=0,
        sessions_updated=0,
        current_atl=0.0,
        current_ctl=0.0,
        current_acwr=None,
    )


def recompute_training_load_from_date(
    db: Session,
    from_date: date,
) -> TrainingLoadRecomputeResult:
    config = _build_training_load_config()

    first_session_date = crud.get_first_session_date(db)
    last_session_date = crud.get_last_session_date(db)
    if first_session_date is None or last_session_date is None:
        return _build_empty_result()

    effective_start = max(from_date, first_session_date)
    initial_atl = 0.0
    initial_ctl = 0.0

    if effective_start > first_session_date:
        previous_day = effective_start - timedelta(days=1)
        previous_state = (
            db.query(models.DailyTrainingLoad)
            .filter(models.DailyTrainingLoad.date == previous_day)
            .first()
        )
        if previous_state is None:
            effective_start = first_session_date
        else:
            initial_atl = float(previous_state.atl or 0.0)
            initial_ctl = float(previous_state.ctl or 0.0)

    sessions = crud.get_sessions_by_date_range(db, effective_start, last_session_date)

    computed = compute_training_load_series(
        sessions=sessions,
        session_zone_time_map={},
        start_date=effective_start,
        end_date=last_session_date,
        config=config,
        initial_atl=initial_atl,
        initial_ctl=initial_ctl,
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

    return TrainingLoadRecomputeResult(
        recomputed_from_date=effective_start,
        recomputed_to_date=last_session_date,
        days_recomputed=len(computed["daily"]),
        sessions_updated=len(sessions),
        current_atl=float(computed.get("current_atl") or 0.0),
        current_ctl=float(computed.get("current_ctl") or 0.0),
        current_acwr=(float(computed["current_acwr"]) if computed.get("current_acwr") is not None else None),
    )


def recompute_training_load_full_history(db: Session) -> TrainingLoadRecomputeResult:
    first_session_date = crud.get_first_session_date(db)
    if first_session_date is None:
        return _build_empty_result()
    return recompute_training_load_from_date(db, first_session_date)
