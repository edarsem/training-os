from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.models import models
from app.core.training_load_defaults import (
    DEFAULT_TRAINING_LOAD_ATL_DAYS,
    DEFAULT_TRAINING_LOAD_CTL_DAYS,
    TRAINING_LOAD_SOFTPLUS4_A,
    TRAINING_LOAD_SOFTPLUS4_B,
    TRAINING_LOAD_SOFTPLUS4_C,
    TRAINING_LOAD_SOFTPLUS4_D,
)


ZONE_NAMES = [
    "very_easy",
    "recovery",
    "aerobic_endurance",
    "aerobic_power",
    "threshold",
    "anaerobic_endurance",
    "anaerobic_power",
]


@dataclass
class TrainingLoadConfig:
    atl_time_constant_days: float = DEFAULT_TRAINING_LOAD_ATL_DAYS
    ctl_time_constant_days: float = DEFAULT_TRAINING_LOAD_CTL_DAYS


def _build_empty_zone_minutes() -> dict[str, int]:
    return {name: 0 for name in ZONE_NAMES}


def compute_training_load_series(
    *,
    sessions: list[models.Session],
    session_zone_time_map: dict[int, dict[str, int | float | None]] | None,
    start_date: date,
    end_date: date,
    config: TrainingLoadConfig,
    initial_atl: float = 0.0,
    initial_ctl: float = 0.0,
) -> dict[str, Any]:
    if end_date < start_date:
        raise ValueError("end_date must be greater than or equal to start_date")

    sessions_by_day: dict[date, list[models.Session]] = {}
    for session in sessions:
        sessions_by_day.setdefault(session.date, []).append(session)

    current = start_date
    atl = float(initial_atl)
    ctl = float(initial_ctl)
    daily_points: list[dict[str, Any]] = []

    while current <= end_date:
        day_sessions = sessions_by_day.get(current, [])

        zone_minutes = _build_empty_zone_minutes()
        missing_hr_minutes = 0
        session_breakdown: list[dict[str, Any]] = []
        day_load = 0.0

        for session in day_sessions:
            duration_minutes = int(session.moving_duration_minutes or session.duration_minutes or 0)
            if duration_minutes <= 0:
                continue

            if session.training_load is not None:
                session_load = float(session.training_load)
                day_load += session_load
                session_breakdown.append(
                    {
                        "session_id": session.id,
                        "type": session.type,
                        "duration_minutes": duration_minutes,
                        "average_hr_bpm": float(session.average_heart_rate_bpm) if session.average_heart_rate_bpm else None,
                        "zone": "hr_stream_softplus4",
                        "zone_coefficient": 0.0,
                        "session_load": round(session_load, 3),
                    }
                )
                continue

            missing_hr_minutes += duration_minutes

            session_breakdown.append(
                {
                    "session_id": session.id,
                    "type": session.type,
                    "duration_minutes": duration_minutes,
                    "average_hr_bpm": float(session.average_heart_rate_bpm) if session.average_heart_rate_bpm else None,
                    "zone": "missing_hr_stream",
                    "zone_coefficient": 0.0,
                    "session_load": 0.0,
                }
            )

        atl = atl + (day_load - atl) / float(config.atl_time_constant_days)
        ctl = ctl + (day_load - ctl) / float(config.ctl_time_constant_days)
        acwr = (atl / ctl) if ctl > 0 else None

        daily_points.append(
            {
                "date": current,
                "load": round(day_load, 3),
                "atl": round(atl, 3),
                "ctl": round(ctl, 3),
                "acwr": round(acwr, 3) if acwr is not None else None,
                "zone_minutes": zone_minutes,
                "missing_hr_minutes": missing_hr_minutes,
                "session_breakdown": session_breakdown,
            }
        )

        current = current + timedelta(days=1)

    return {
        "daily": daily_points,
        "current_atl": daily_points[-1]["atl"] if daily_points else 0.0,
        "current_ctl": daily_points[-1]["ctl"] if daily_points else 0.0,
        "current_acwr": daily_points[-1]["acwr"] if daily_points else None,
        "config": {
            "function": "softplus4",
            "softplus4_a": TRAINING_LOAD_SOFTPLUS4_A,
            "softplus4_b": TRAINING_LOAD_SOFTPLUS4_B,
            "softplus4_c": TRAINING_LOAD_SOFTPLUS4_C,
            "softplus4_d": TRAINING_LOAD_SOFTPLUS4_D,
            "atl_time_constant_days": config.atl_time_constant_days,
            "ctl_time_constant_days": config.ctl_time_constant_days,
        },
    }
