from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.models import models
from app.core.training_load_defaults import (
    DEFAULT_TRAINING_LOAD_ATL_DAYS,
    DEFAULT_TRAINING_LOAD_CTL_DAYS,
    DEFAULT_TRAINING_LOAD_ZONE_BOUNDARIES_PCT,
    DEFAULT_TRAINING_LOAD_ZONE_COEFFICIENTS,
)


ZONE_NAMES = [
    "recovery",
    "aerobic_endurance",
    "aerobic_power",
    "threshold",
    "anaerobic_endurance",
    "anaerobic_power",
]


@dataclass
class TrainingLoadConfig:
    threshold_hr: float
    zone_coefficients: list[float] = field(default_factory=lambda: list(DEFAULT_TRAINING_LOAD_ZONE_COEFFICIENTS))
    atl_time_constant_days: float = DEFAULT_TRAINING_LOAD_ATL_DAYS
    ctl_time_constant_days: float = DEFAULT_TRAINING_LOAD_CTL_DAYS


def get_zone_index(average_hr_bpm: float, threshold_hr: float) -> int:
    if threshold_hr <= 0:
        return 0

    pct = (average_hr_bpm / threshold_hr) * 100.0
    z1_max, z2_max, z3_max, z4_max, z5_max = DEFAULT_TRAINING_LOAD_ZONE_BOUNDARIES_PCT

    if pct < z1_max:
        return 0
    if pct <= z2_max:
        return 1
    if pct <= z3_max:
        return 2
    if pct <= z4_max:
        return 3
    if pct <= z5_max:
        return 4
    return 5


def _build_empty_zone_minutes() -> dict[str, int]:
    return {name: 0 for name in ZONE_NAMES}


def compute_training_load_series(
    *,
    sessions: list[models.Session],
    session_zone_time_map: dict[int, dict[str, int]] | None,
    start_date: date,
    end_date: date,
    config: TrainingLoadConfig,
) -> dict[str, Any]:
    if end_date < start_date:
        raise ValueError("end_date must be greater than or equal to start_date")

    sessions_by_day: dict[date, list[models.Session]] = {}
    for session in sessions:
        sessions_by_day.setdefault(session.date, []).append(session)

    current = start_date
    atl = 0.0
    ctl = 0.0
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

            session_zone_seconds = None
            if session_zone_time_map and session.id is not None:
                session_zone_seconds = session_zone_time_map.get(int(session.id))

            if session_zone_seconds:
                zone_seconds_values = [
                    int(session_zone_seconds.get("zone_1_seconds", 0)),
                    int(session_zone_seconds.get("zone_2_seconds", 0)),
                    int(session_zone_seconds.get("zone_3_seconds", 0)),
                    int(session_zone_seconds.get("zone_4_seconds", 0)),
                    int(session_zone_seconds.get("zone_5_seconds", 0)),
                    int(session_zone_seconds.get("zone_6_seconds", 0)),
                ]
                total_zone_seconds = sum(max(0, value) for value in zone_seconds_values)
                if total_zone_seconds > 0:
                    session_load = 0.0
                    for zone_idx, seconds_value in enumerate(zone_seconds_values):
                        seconds_in_zone = max(0, int(seconds_value))
                        if seconds_in_zone <= 0:
                            continue

                        minutes_in_zone = float(seconds_in_zone) / 60.0
                        zone_minutes[ZONE_NAMES[zone_idx]] += int(round(minutes_in_zone))
                        session_load += minutes_in_zone * float(config.zone_coefficients[zone_idx])

                    day_load += session_load
                    duration_from_stream_minutes = int(round(float(total_zone_seconds) / 60.0))
                    effective_duration_minutes = max(duration_minutes, duration_from_stream_minutes)

                    session_breakdown.append(
                        {
                            "session_id": session.id,
                            "type": session.type,
                            "duration_minutes": effective_duration_minutes,
                            "average_hr_bpm": float(session.average_heart_rate_bpm) if session.average_heart_rate_bpm else None,
                            "zone": "stream_zones",
                            "zone_coefficient": round(session_load / max(1.0, float(effective_duration_minutes)), 3),
                            "session_load": round(session_load, 3),
                        }
                    )
                    continue

            avg_hr = session.average_heart_rate_bpm
            if avg_hr is None or avg_hr <= 0:
                missing_hr_minutes += duration_minutes
                session_breakdown.append(
                    {
                        "session_id": session.id,
                        "type": session.type,
                        "duration_minutes": duration_minutes,
                        "average_hr_bpm": None,
                        "zone": None,
                        "zone_coefficient": 0.0,
                        "session_load": 0.0,
                    }
                )
                continue

            zone_index = get_zone_index(float(avg_hr), config.threshold_hr)
            zone_name = ZONE_NAMES[zone_index]
            zone_coef = float(config.zone_coefficients[zone_index])
            zone_minutes[zone_name] += duration_minutes

            session_load = duration_minutes * zone_coef
            day_load += session_load

            session_breakdown.append(
                {
                    "session_id": session.id,
                    "type": session.type,
                    "duration_minutes": duration_minutes,
                    "average_hr_bpm": float(avg_hr),
                    "zone": zone_name,
                    "zone_coefficient": zone_coef,
                    "session_load": round(session_load, 3),
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
            "threshold_hr": config.threshold_hr,
            "zone_coefficients": config.zone_coefficients,
            "atl_time_constant_days": config.atl_time_constant_days,
            "ctl_time_constant_days": config.ctl_time_constant_days,
        },
    }
