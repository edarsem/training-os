from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session as DBSession

from app.crud import crud


def _iso_anchor_from_date(value: date) -> tuple[int, int]:
    iso = value.isocalendar()
    return int(iso[0]), int(iso[1])


def _week_window(year: int, week_number: int) -> tuple[date, date]:
    start = date.fromisocalendar(year, week_number, 1)
    return start, start + timedelta(days=6)


def _truncate_text(value: str | None, max_chars: int = 220) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _safe_per_7_days(total: float, number_of_days: int) -> float:
    if number_of_days <= 0:
        return 0.0
    return round((float(total) * 7.0) / float(number_of_days), 2)


def _resolve_temporal_reference_common(
    *,
    temporal_ref: str,
    now_iso_date: str,
    resolver: Callable[[str, str, str | None], dict[str, Any]],
    language: str | None = None,
) -> dict[str, Any]:
    resolved = resolver(temporal_ref, now_iso_date, language)
    mode = str(resolved.get("mode") or "date").strip().lower()

    if mode == "range":
        start_iso = str(resolved.get("range_start_iso") or now_iso_date)
        end_iso = str(resolved.get("range_end_iso") or start_iso)
        try:
            start_date = date.fromisoformat(start_iso)
            end_date = date.fromisoformat(end_iso)
        except ValueError:
            fallback = date.fromisoformat(now_iso_date)
            start_date = fallback
            end_date = fallback

        if end_date < start_date:
            start_date, end_date = end_date, start_date

        return {
            "mode": "range",
            "range_start_iso": start_date.isoformat(),
            "range_end_iso": end_date.isoformat(),
            "label": resolved.get("label") or temporal_ref,
        }

    reference_iso = str(resolved.get("reference_date_iso") or now_iso_date)
    try:
        reference_date = date.fromisoformat(reference_iso)
    except ValueError:
        reference_date = date.fromisoformat(now_iso_date)

    return {
        "mode": "date",
        "reference_date_iso": reference_date.isoformat(),
        "label": resolved.get("label") or temporal_ref,
    }


def resolve_time_reference_tool(
    *,
    query: str,
    now_iso_date: str,
    resolver: Callable[[str, str, str | None], dict[str, Any]],
    language: str | None = None,
) -> dict[str, Any]:
    return _resolve_temporal_reference_common(
        temporal_ref=query,
        now_iso_date=now_iso_date,
        resolver=resolver,
        language=language,
    )


def _resolve_day_date_iso(
    *,
    date_iso: str | None,
    temporal_ref: str | None,
    now_iso_date: str | None,
    language: str | None,
    resolver: Callable[[str, str, str | None], dict[str, Any]] | None,
) -> tuple[str, dict[str, Any] | None]:
    if date_iso:
        parsed = date.fromisoformat(str(date_iso))
        return parsed.isoformat(), None

    if temporal_ref:
        if resolver is None:
            raise ValueError("temporal_ref requires a time_resolver")
        if not now_iso_date:
            raise ValueError("temporal_ref requires now_iso_date")

        resolved = _resolve_temporal_reference_common(
            temporal_ref=str(temporal_ref),
            now_iso_date=str(now_iso_date),
            resolver=resolver,
            language=language,
        )

        if resolved.get("mode") == "range":
            reference = str(resolved.get("range_start_iso"))
        else:
            reference = str(resolved.get("reference_date_iso"))

        return date.fromisoformat(reference).isoformat(), {
            "temporal_ref": temporal_ref,
            "resolved": resolved,
        }

    raise ValueError("Either date_iso or temporal_ref must be provided")


def _resolve_block_range_iso(
    *,
    start_iso: str | None,
    end_iso: str | None,
    temporal_ref: str | None,
    now_iso_date: str | None,
    language: str | None,
    resolver: Callable[[str, str, str | None], dict[str, Any]] | None,
) -> tuple[str, str, dict[str, Any] | None]:
    if start_iso and end_iso:
        start_date = date.fromisoformat(str(start_iso))
        end_date = date.fromisoformat(str(end_iso))
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        return start_date.isoformat(), end_date.isoformat(), None

    if temporal_ref:
        if resolver is None:
            raise ValueError("temporal_ref requires a time_resolver")
        if not now_iso_date:
            raise ValueError("temporal_ref requires now_iso_date")

        resolved = _resolve_temporal_reference_common(
            temporal_ref=str(temporal_ref),
            now_iso_date=str(now_iso_date),
            resolver=resolver,
            language=language,
        )

        if resolved.get("mode") == "range":
            start_date = date.fromisoformat(str(resolved.get("range_start_iso")))
            end_date = date.fromisoformat(str(resolved.get("range_end_iso")))
        else:
            start_date = date.fromisoformat(str(resolved.get("reference_date_iso")))
            end_date = start_date

        if end_date < start_date:
            start_date, end_date = end_date, start_date

        return start_date.isoformat(), end_date.isoformat(), {
            "temporal_ref": temporal_ref,
            "resolved": resolved,
        }

    raise ValueError("Either start_iso/end_iso or temporal_ref must be provided")


def get_week_summary_tool(
    db: DBSession,
    *,
    date_iso: str,
    include_sessions: bool = False,
    temporal_resolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reference_date = date.fromisoformat(date_iso)
    anchor_year, anchor_week = _iso_anchor_from_date(reference_date)
    start_date, end_date = _week_window(anchor_year, anchor_week)
    sessions = crud.get_sessions_by_date_range(db, start_date, end_date)
    day_notes = crud.get_day_notes_by_date_range(db, start_date, end_date)
    plan = crud.get_weekly_plan(db, anchor_year, anchor_week)

    total_duration = int(sum((s.duration_minutes or 0) for s in sessions))
    total_distance = round(sum((s.distance_km or 0) for s in sessions if s.type in ["run", "trail"]), 1)
    total_elevation = int(sum((s.elevation_gain_m or 0) for s in sessions if s.type in ["run", "trail", "hike"]))

    payload: dict[str, Any] = {
        "year": anchor_year,
        "week_number": anchor_week,
        "date_start": start_date.isoformat(),
        "date_end": end_date.isoformat(),
        "totals": {
            "total_sessions": len(sessions),
            "total_duration_minutes": total_duration,
            "total_distance_km": total_distance,
            "total_elevation_gain_m": total_elevation,
        },
        "plan": {
            "description": plan.description if plan else None,
            "target_distance_km": plan.target_distance_km if plan else None,
            "target_sessions": plan.target_sessions if plan else None,
            "tags": plan.tags if plan else None,
        },
        "day_notes": [
            {
                "date": item.date.isoformat(),
                "note": item.note,
            }
            for item in day_notes
        ],
    }

    if include_sessions:
        payload["sessions"] = [
            {
                "date": s.date.isoformat(),
                "type": s.type,
                "moving_duration_minutes": s.moving_duration_minutes,
                "duration_minutes": s.duration_minutes,
                "distance_km": s.distance_km,
                "elevation_gain_m": s.elevation_gain_m,
                "notes": _truncate_text(s.notes, max_chars=140),
            }
            for s in sessions
        ]

    if temporal_resolution:
        payload["temporal_resolution"] = temporal_resolution

    return payload


def get_day_details_tool(
    db: DBSession,
    *,
    date_iso: str,
    truncate_notes_chars: int = 220,
    temporal_resolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target_date = date.fromisoformat(date_iso)
    sessions = crud.get_sessions_by_date_range(db, target_date, target_date)
    day_note = crud.get_day_note(db, target_date)

    payload = {
        "date": target_date.isoformat(),
        "day_note": day_note.note if day_note else None,
        "totals": {
            "total_sessions": len(sessions),
            "total_duration_minutes": int(sum((s.duration_minutes or 0) for s in sessions)),
            "total_moving_minutes": int(sum((s.moving_duration_minutes or 0) for s in sessions)),
            "total_distance_km": round(sum((s.distance_km or 0) for s in sessions if s.type in ["run", "trail"]), 1),
            "total_elevation_gain_m": int(sum((s.elevation_gain_m or 0) for s in sessions if s.type in ["run", "trail", "hike"])),
        },
        "sessions": [
            {
                "id": s.id,
                "external_id": s.external_id,
                "type": s.type,
                "start_time": s.start_time.isoformat() if s.start_time else None,
                "duration_minutes": s.duration_minutes,
                "elapsed_duration_minutes": s.elapsed_duration_minutes,
                "moving_duration_minutes": s.moving_duration_minutes,
                "distance_km": s.distance_km,
                "elevation_gain_m": s.elevation_gain_m,
                "perceived_intensity": s.perceived_intensity,
                "notes": _truncate_text(s.notes, max_chars=max(40, int(truncate_notes_chars))),
            }
            for s in sessions
        ],
    }

    if temporal_resolution:
        payload["temporal_resolution"] = temporal_resolution

    return payload


def get_session_details_tool(
    db: DBSession,
    *,
    session_id: int,
) -> dict[str, Any]:
    session = crud.get_session_by_id(db, int(session_id))
    if not session:
        return {
            "error": "session_not_found",
            "session_id": int(session_id),
        }

    return {
        "id": session.id,
        "external_id": session.external_id,
        "date": session.date.isoformat(),
        "start_time": session.start_time.isoformat() if session.start_time else None,
        "type": session.type,
        "duration_minutes": session.duration_minutes,
        "elapsed_duration_minutes": session.elapsed_duration_minutes,
        "moving_duration_minutes": session.moving_duration_minutes,
        "distance_km": session.distance_km,
        "elevation_gain_m": session.elevation_gain_m,
        "average_pace_min_per_km": session.average_pace_min_per_km,
        "average_heart_rate_bpm": session.average_heart_rate_bpm,
        "max_heart_rate_bpm": session.max_heart_rate_bpm,
        "perceived_intensity": session.perceived_intensity,
        "notes": session.notes,
    }


def get_block_summary_tool(
    db: DBSession,
    *,
    start_iso: str,
    end_iso: str,
    temporal_resolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    start_date = date.fromisoformat(start_iso)
    end_date = date.fromisoformat(end_iso)
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    sessions = crud.get_sessions_by_date_range(db, start_date, end_date)
    day_notes = crud.get_day_notes_by_date_range(db, start_date, end_date)

    number_of_days = (end_date - start_date).days + 1
    active_training_days = len({s.date for s in sessions})
    noted_days = len({n.date for n in day_notes})

    run_trail_sessions = [s for s in sessions if s.type in ["run", "trail"]]
    bike_sessions = [s for s in sessions if s.type == "bike"]
    strength_sessions = [s for s in sessions if s.type == "strength"]

    total_run_distance_km = round(sum((s.distance_km or 0.0) for s in run_trail_sessions), 2)
    total_bike_distance_km = round(sum((s.distance_km or 0.0) for s in bike_sessions), 2)
    total_elevation_gain_m = int(sum((s.elevation_gain_m or 0) for s in sessions if s.type in ["run", "trail", "hike"]))
    total_strength_time_minutes = int(sum((s.moving_duration_minutes or s.duration_minutes or 0) for s in strength_sessions))
    total_time_minutes = int(sum((s.moving_duration_minutes or s.duration_minutes or 0) for s in sessions))

    longest_run_or_trail: dict[str, Any] | None = None
    if run_trail_sessions:
        best = max(run_trail_sessions, key=lambda s: (s.distance_km or 0.0, s.duration_minutes or 0, s.id or 0))
        longest_run_or_trail = {
            "session_id": best.id,
            "date": best.date.isoformat(),
            "type": best.type,
            "distance_km": best.distance_km,
            "duration_minutes": best.duration_minutes,
            "moving_duration_minutes": best.moving_duration_minutes,
            "elevation_gain_m": best.elevation_gain_m,
        }

    payload = {
        "date_start": start_date.isoformat(),
        "date_end": end_date.isoformat(),
        "number_of_days": number_of_days,
        "active_training_days": active_training_days,
        "days_with_notes": noted_days,
        "total_sessions": len(sessions),
        "longest_run_or_trail": longest_run_or_trail,
        "totals": {
            "run_distance_km": total_run_distance_km,
            "bike_distance_km": total_bike_distance_km,
            "elevation_gain_m": total_elevation_gain_m,
            "strength_time_minutes": total_strength_time_minutes,
            "total_time_minutes": total_time_minutes,
        },
        "normalized_per_7_days": {
            "run_distance_km": _safe_per_7_days(total_run_distance_km, number_of_days),
            "bike_distance_km": _safe_per_7_days(total_bike_distance_km, number_of_days),
            "elevation_gain_m": _safe_per_7_days(total_elevation_gain_m, number_of_days),
            "strength_time_minutes": _safe_per_7_days(total_strength_time_minutes, number_of_days),
            "total_time_minutes": _safe_per_7_days(total_time_minutes, number_of_days),
        },
    }

    if temporal_resolution:
        payload["temporal_resolution"] = temporal_resolution

    return payload


def get_mcp_tools_schema() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "resolve_time_reference",
                "description": "Resolve natural language time references into either a single date or an explicit date range. Use it to resolve ambiguous or relative temporal expressions (e.g., 'last monday', 'next week', 'month of janvier') into explicit ISO date values. When the reference implies a period longer than a week or is ambiguous, return a range. Always return a label that can be used to refer to the resolved time in the final answer.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "now_iso_date": {"type": "string"},
                        "language": {"type": "string"},
                    },
                    "required": ["query", "now_iso_date"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_week_summary",
                "description": "Get summary metrics, plan and notes for the ISO week that contains a date. Use date_iso when possible with any day of this week; otherwise provide temporal_ref with now_iso_date.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date_iso": {"type": "string"},
                        "temporal_ref": {"type": "string"},
                        "now_iso_date": {"type": "string"},
                        "language": {"type": "string"},
                        "include_sessions": {"type": "boolean"},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_day_details",
                "description": "Get details for one day, including per-session details with moving time and truncated notes. Use date_iso when possible; otherwise temporal_ref + now_iso_date.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date_iso": {"type": "string"},
                        "temporal_ref": {"type": "string"},
                        "now_iso_date": {"type": "string"},
                        "language": {"type": "string"},
                        "truncate_notes_chars": {"type": "integer", "minimum": 40, "maximum": 1000},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_session_details",
                "description": "Get all available details for one session by session id.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "integer"},
                    },
                    "required": ["session_id"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_block_summary",
                "description": "Get aggregated metrics for a date range (multi-week block), including normalized metrics per 7 days for block comparison. Use start_iso/end_iso when explicit; otherwise temporal_ref + now_iso_date.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_iso": {"type": "string"},
                        "end_iso": {"type": "string"},
                        "temporal_ref": {"type": "string"},
                        "now_iso_date": {"type": "string"},
                        "language": {"type": "string"},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit_final_answer",
                "description": "Submit the final user-facing answer once tool usage is complete.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string"},
                    },
                    "required": ["answer"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def execute_mcp_tool(
    db: DBSession,
    *,
    name: str,
    arguments: dict[str, Any],
    time_resolver: Callable[[str, str, str | None], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if name == "resolve_time_reference":
        if time_resolver is None:
            raise ValueError("resolve_time_reference requires a time_resolver")
        return resolve_time_reference_tool(
            query=str(arguments.get("query") or ""),
            now_iso_date=str(arguments.get("now_iso_date") or date.today().isoformat()),
            language=arguments.get("language"),
            resolver=time_resolver,
        )

    if name == "get_week_summary":
        date_iso, temporal_resolution = _resolve_day_date_iso(
            date_iso=arguments.get("date_iso"),
            temporal_ref=arguments.get("temporal_ref"),
            now_iso_date=arguments.get("now_iso_date"),
            language=arguments.get("language"),
            resolver=time_resolver,
        )
        include_sessions = bool(arguments.get("include_sessions", False))
        return get_week_summary_tool(
            db,
            date_iso=date_iso,
            include_sessions=include_sessions,
            temporal_resolution=temporal_resolution,
        )

    if name == "get_day_details":
        date_iso, temporal_resolution = _resolve_day_date_iso(
            date_iso=arguments.get("date_iso"),
            temporal_ref=arguments.get("temporal_ref"),
            now_iso_date=arguments.get("now_iso_date"),
            language=arguments.get("language"),
            resolver=time_resolver,
        )
        truncate_notes_chars = int(arguments.get("truncate_notes_chars", 220))
        return get_day_details_tool(
            db,
            date_iso=date_iso,
            truncate_notes_chars=truncate_notes_chars,
            temporal_resolution=temporal_resolution,
        )

    if name == "get_session_details":
        session_id = int(arguments["session_id"])
        return get_session_details_tool(
            db,
            session_id=session_id,
        )

    if name == "get_block_summary":
        start_iso, end_iso, temporal_resolution = _resolve_block_range_iso(
            start_iso=arguments.get("start_iso"),
            end_iso=arguments.get("end_iso"),
            temporal_ref=arguments.get("temporal_ref"),
            now_iso_date=arguments.get("now_iso_date"),
            language=arguments.get("language"),
            resolver=time_resolver,
        )
        return get_block_summary_tool(
            db,
            start_iso=start_iso,
            end_iso=end_iso,
            temporal_resolution=temporal_resolution,
        )

    if name == "submit_final_answer":
        return {
            "final_answer": str(arguments.get("answer") or "").strip(),
            "status": "ok",
        }

    raise ValueError(f"Unknown MCP tool '{name}'")
