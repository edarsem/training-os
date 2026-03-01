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


def _format_duration_hours(total_minutes: int) -> str:
    minutes = int(total_minutes or 0)
    hours = minutes // 60
    rem = minutes % 60
    if rem == 0:
        return f"{hours}h"
    return f"{hours}h{str(rem).zfill(2)}"


def _day_label(value: date) -> str:
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return weekdays[value.weekday()]


def _ordinal_day(value: date) -> str:
    day_num = value.day
    if 10 <= day_num % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day_num % 10, "th")
    return f"{day_num}{suffix}"


def _month_day_label(value: date) -> str:
    return f"{value.strftime('%B')} {_ordinal_day(value)}"


def _render_week_summary_text(payload: dict[str, Any]) -> str:
    totals = payload.get("totals", {})
    lines = [
        f"{payload.get('date_start')} to {payload.get('date_end')} week summary:",
        (
            f"{totals.get('total_sessions', 0)} sessions, "
            f"{_format_duration_hours(int(totals.get('total_duration_minutes') or 0))}, "
            f"{totals.get('total_distance_km', 0)} km and {totals.get('total_elevation_gain_m', 0)} m+"
        ),
    ]

    plan = payload.get("plan") or {}
    lines.append(f"Week plan: {plan.get('description') or 'None'}")

    day_notes = payload.get("day_notes") or []
    if day_notes:
        lines.append("Day notes:")
        for note in day_notes:
            lines.append(f"- {note.get('date')}: {note.get('note') or ''}")

    sessions = payload.get("sessions") or []
    if sessions:
        lines.append("Main sessions:")
        for item in sessions:
            session_date = date.fromisoformat(str(item.get("date")))
            distance = item.get("distance_km")
            elev = item.get("elevation_gain_m")
            base = (
                f"{_day_label(session_date)} the {_ordinal_day(session_date)}, "
                f"{_format_duration_hours(int(item.get('moving_duration_minutes') or item.get('duration_minutes') or 0))} {item.get('type')}"
            )
            if distance:
                base += f", {distance} km"
            if elev:
                base += f", {elev} m+"
            lines.append(base)
            if item.get("notes"):
                lines.append(f"note: {item.get('notes')}")

    return "\n".join(lines)


def _render_day_details_text(payload: dict[str, Any]) -> str:
    totals = payload.get("totals", {})
    lines = [
        f"{payload.get('date')} day details:",
        (
            f"{totals.get('total_sessions', 0)} sessions, "
            f"{_format_duration_hours(int(totals.get('total_duration_minutes') or 0))} total "
            f"({_format_duration_hours(int(totals.get('total_moving_minutes') or 0))} moving), "
            f"{totals.get('total_distance_km', 0)} km, {totals.get('total_elevation_gain_m', 0)} m+"
        ),
    ]
    day_note = payload.get("day_note")
    if day_note:
        lines.append(f"Day note: {day_note}")

    sessions = payload.get("sessions") or []
    if sessions:
        lines.append("Sessions:")
        for item in sessions:
            base = f"- {item.get('type')} #{item.get('id')}: {_format_duration_hours(int(item.get('moving_duration_minutes') or item.get('duration_minutes') or 0))}"
            if item.get("distance_km"):
                base += f", {item.get('distance_km')} km"
            if item.get("elevation_gain_m"):
                base += f", {item.get('elevation_gain_m')} m+"
            lines.append(base)
            if item.get("notes"):
                lines.append(f"  note: {item.get('notes')}")

    return "\n".join(lines)


def _render_session_details_text(payload: dict[str, Any]) -> str:
    if payload.get("error"):
        return f"Session {payload.get('session_id')} not found."

    lines = [
        f"Session #{payload.get('id')} ({payload.get('type')}) on {payload.get('date')}",
        f"Moving: {_format_duration_hours(int(payload.get('moving_duration_minutes') or payload.get('duration_minutes') or 0))}",
        f"Elapsed: {_format_duration_hours(int(payload.get('elapsed_duration_minutes') or payload.get('duration_minutes') or 0))}",
    ]
    if payload.get("distance_km"):
        lines.append(f"Distance: {payload.get('distance_km')} km")
    if payload.get("elevation_gain_m"):
        lines.append(f"Elevation: {payload.get('elevation_gain_m')} m+")
    if payload.get("average_pace_min_per_km"):
        lines.append(f"Avg pace: {payload.get('average_pace_min_per_km')} min/km")
    if payload.get("average_heart_rate_bpm"):
        hr = f"{payload.get('average_heart_rate_bpm')}"
        if payload.get("max_heart_rate_bpm"):
            hr += f"/{payload.get('max_heart_rate_bpm')}"
        lines.append(f"HR avg/max: {hr}")
    if payload.get("perceived_intensity"):
        lines.append(f"Intensity: {payload.get('perceived_intensity')}/10")
    if payload.get("notes"):
        lines.append(f"Notes: {payload.get('notes')}")
    return "\n".join(lines)


def _render_block_summary_text(payload: dict[str, Any]) -> str:
    totals = payload.get("totals", {})
    normalized = payload.get("normalized_per_7_days", {})
    lines = [
        f"Block summary {payload.get('date_start')} to {payload.get('date_end')}:",
        (
            f"{payload.get('total_sessions', 0)} sessions over {payload.get('number_of_days', 0)} days "
            f"({payload.get('active_training_days', 0)} active days)."
        ),
        (
            f"Totals: run {totals.get('run_distance_km', 0)} km, bike {totals.get('bike_distance_km', 0)} km, "
            f"elevation {totals.get('elevation_gain_m', 0)} m+, "
            f"strength {_format_duration_hours(int(totals.get('strength_time_minutes') or 0))}, "
            f"time {_format_duration_hours(int(totals.get('total_time_minutes') or 0))}."
        ),
        (
            f"Normalized per 7 days: run {normalized.get('run_distance_km', 0)} km, bike {normalized.get('bike_distance_km', 0)} km, "
            f"elevation {normalized.get('elevation_gain_m', 0)} m+, strength {normalized.get('strength_time_minutes', 0)} min, "
            f"time {normalized.get('total_time_minutes', 0)} min."
        ),
    ]

    longest = payload.get("longest_run_or_trail")
    if longest:
        lines.append(
            f"Longest run/trail: {longest.get('type')} on {longest.get('date')}, "
            f"{longest.get('distance_km') or 0} km, {_format_duration_hours(int(longest.get('moving_duration_minutes') or longest.get('duration_minutes') or 0))}."
        )

    weekly_breakdown = payload.get("weekly_breakdown") or []
    if weekly_breakdown:
        lines.append("Weekly trend:")
        for item in weekly_breakdown:
            line = (
                f"- Week of {item.get('week_start')}: run/trail {item.get('run_trail_distance_km', 0)} km, "
                f"{item.get('run_trail_elevation_gain_m', 0)} m+, {item.get('total_sessions', 0)} sessions"
            )
            longest_week = item.get("longest_run_or_trail")
            if longest_week:
                line += (
                    f". Longest on {longest_week.get('weekday')}: {longest_week.get('distance_km') or 0} km, "
                    f"{longest_week.get('elevation_gain_m') or 0} m+ in "
                    f"{_format_duration_hours(int(longest_week.get('moving_duration_minutes') or longest_week.get('duration_minutes') or 0))} "
                    f"[session #{longest_week.get('session_id')}]"
                )
            lines.append(line)

    return "\n".join(lines)


def _render_recent_weeks_summary_text(payload: dict[str, Any]) -> str:
    now_iso = payload.get("now_iso_date")
    weeks = payload.get("weeks") or []
    lines = [f"Recent {len(weeks)} weeks summary (anchor: {now_iso}):"]
    for item in weeks:
        line = (
            f"Week of {_month_day_label(date.fromisoformat(str(item.get('week_start'))))}: "
            f"{item.get('run_trail_distance_km', 0)} km, {item.get('run_trail_elevation_gain_m', 0)} m+ "
            f"in {item.get('total_sessions', 0)} sessions ({_format_duration_hours(int(item.get('total_duration_minutes') or 0))})."
        )
        longest = item.get("longest_run_or_trail")
        if longest:
            line += (
                f" Longest on {longest.get('weekday')}, {longest.get('distance_km') or 0} km, "
                f"{longest.get('elevation_gain_m') or 0} m+ in "
                f"{_format_duration_hours(int(longest.get('moving_duration_minutes') or longest.get('duration_minutes') or 0))} "
                f"[session #{longest.get('session_id')}]"
            )
        lines.append(line)
    return "\n".join(lines)


def _resolve_temporal_reference_common(
    *,
    temporal_ref: str,
    now_iso_date: str,
    resolver: Callable[[str, str, str | None], dict[str, Any]],
    language: str | None = None,
) -> dict[str, Any]:
    resolved = resolver(temporal_ref, now_iso_date, language)
    mode = str(resolved.get("mode") or "date").strip().lower()

    if mode not in {"date", "range"}:
        return {
            "mode": "unresolved",
            "label": resolved.get("label") or temporal_ref,
            "error": resolved.get("error") or "unable_to_resolve_time_reference",
        }

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

    reference_iso = str(resolved.get("reference_date_iso") or "")
    if not reference_iso:
        return {
            "mode": "unresolved",
            "label": resolved.get("label") or temporal_ref,
            "error": "missing_reference_date_iso",
        }
    try:
        reference_date = date.fromisoformat(reference_iso)
    except ValueError:
        return {
            "mode": "unresolved",
            "label": resolved.get("label") or temporal_ref,
            "error": "invalid_reference_date_iso",
        }

    return {
        "mode": "date",
        "reference_date_iso": reference_date.isoformat(),
        "label": resolved.get("label") or temporal_ref,
    }


def resolve_time_reference_tool(
    *,
    temporal_ref: str,
    now_iso_date: str,
    resolver: Callable[[str, str, str | None], dict[str, Any]],
    language: str | None = None,
) -> dict[str, Any]:
    return _resolve_temporal_reference_common(
        temporal_ref=temporal_ref,
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

        if resolved.get("mode") == "unresolved":
            return "", {
                "temporal_ref": temporal_ref,
                "resolved": resolved,
            }

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

        if resolved.get("mode") == "unresolved":
            return "", "", {
                "temporal_ref": temporal_ref,
                "resolved": resolved,
            }

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
    output_mode: str = "text",
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

    if output_mode == "json":
        return payload

    return {"text": _render_week_summary_text(payload)}


def get_day_details_tool(
    db: DBSession,
    *,
    date_iso: str,
    truncate_notes_chars: int = 220,
    temporal_resolution: dict[str, Any] | None = None,
    output_mode: str = "text",
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

    if output_mode == "json":
        return payload

    return {"text": _render_day_details_text(payload)}


def get_session_details_tool(
    db: DBSession,
    *,
    session_id: int,
    output_mode: str = "text",
) -> dict[str, Any]:
    session = crud.get_session_by_id(db, int(session_id))
    if not session:
        return {
            "error": "session_not_found",
            "session_id": int(session_id),
        }

    payload = {
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

    if output_mode == "json":
        return payload

    return {"text": _render_session_details_text(payload)}


def get_block_summary_tool(
    db: DBSession,
    *,
    start_iso: str,
    end_iso: str,
    temporal_resolution: dict[str, Any] | None = None,
    output_mode: str = "text",
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

    first_week_start = start_date - timedelta(days=start_date.weekday())
    last_week_start = end_date - timedelta(days=end_date.weekday())
    weekly_breakdown: list[dict[str, Any]] = []
    cursor = first_week_start
    while cursor <= last_week_start:
        window_start = max(cursor, start_date)
        window_end = min(cursor + timedelta(days=6), end_date)
        week_sessions = [s for s in sessions if window_start <= s.date <= window_end]
        week_run_trail = [s for s in week_sessions if s.type in ["run", "trail"]]

        week_longest: dict[str, Any] | None = None
        if week_run_trail:
            best_week = max(week_run_trail, key=lambda s: (s.distance_km or 0.0, s.duration_minutes or 0, s.id or 0))
            week_longest = {
                "session_id": best_week.id,
                "date": best_week.date.isoformat(),
                "weekday": _day_label(best_week.date),
                "type": best_week.type,
                "distance_km": best_week.distance_km,
                "duration_minutes": best_week.duration_minutes,
                "moving_duration_minutes": best_week.moving_duration_minutes,
                "elevation_gain_m": best_week.elevation_gain_m,
            }

        weekly_breakdown.append(
            {
                "week_start": cursor.isoformat(),
                "week_end": (cursor + timedelta(days=6)).isoformat(),
                "total_sessions": len(week_sessions),
                "run_trail_distance_km": round(sum((s.distance_km or 0.0) for s in week_run_trail), 2),
                "run_trail_elevation_gain_m": int(sum((s.elevation_gain_m or 0) for s in week_run_trail)),
                "longest_run_or_trail": week_longest,
            }
        )
        cursor = cursor + timedelta(days=7)

    payload = {
        "date_start": start_date.isoformat(),
        "date_end": end_date.isoformat(),
        "number_of_days": number_of_days,
        "active_training_days": active_training_days,
        "days_with_notes": noted_days,
        "total_sessions": len(sessions),
        "longest_run_or_trail": longest_run_or_trail,
        "weekly_breakdown": weekly_breakdown,
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

    if output_mode == "json":
        return payload

    return {"text": _render_block_summary_text(payload)}


def get_recent_weeks_summary_tool(
    db: DBSession,
    *,
    weeks_count: int = 4,
    now_iso_date: str | None = None,
    output_mode: str = "text",
) -> dict[str, Any]:
    count = max(1, min(int(weeks_count), 24))
    now_date = date.fromisoformat(now_iso_date) if now_iso_date else date.today()
    current_week_start = now_date - timedelta(days=now_date.weekday())

    weeks: list[dict[str, Any]] = []
    for offset in range(count):
        week_start = current_week_start - timedelta(days=7 * offset)
        week_end = week_start + timedelta(days=6)
        effective_end = min(week_end, now_date)
        week_sessions = crud.get_sessions_by_date_range(db, week_start, effective_end)
        run_trail = [s for s in week_sessions if s.type in ["run", "trail"]]

        longest: dict[str, Any] | None = None
        if run_trail:
            best = max(run_trail, key=lambda s: (s.distance_km or 0.0, s.duration_minutes or 0, s.id or 0))
            longest = {
                "session_id": best.id,
                "date": best.date.isoformat(),
                "weekday": _day_label(best.date),
                "type": best.type,
                "distance_km": best.distance_km,
                "elevation_gain_m": best.elevation_gain_m,
                "duration_minutes": best.duration_minutes,
                "moving_duration_minutes": best.moving_duration_minutes,
            }

        weeks.append(
            {
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "effective_end": effective_end.isoformat(),
                "total_sessions": len(week_sessions),
                "total_duration_minutes": int(sum((s.duration_minutes or 0) for s in week_sessions)),
                "run_trail_distance_km": round(sum((s.distance_km or 0.0) for s in run_trail), 2),
                "run_trail_elevation_gain_m": int(sum((s.elevation_gain_m or 0) for s in run_trail)),
                "longest_run_or_trail": longest,
            }
        )

    payload = {
        "now_iso_date": now_date.isoformat(),
        "weeks_count": count,
        "weeks": weeks,
    }

    if output_mode == "json":
        return payload

    return {"text": _render_recent_weeks_summary_text(payload)}


def get_mcp_tools_schema() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "get_week_summary",
                "description": "Get an overview of one week. Aggregated metrics, plan and notes (sessions can also be included) for the week containing a given date. Use date_iso when possible with any day of this week; otherwise provide temporal_ref with now_iso_date.",
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
                "description": "Get aggregated metrics for a date range (multi-week to months block), including normalized metrics per 7 days for block comparison. Use start_iso/end_iso when explicit; otherwise temporal_ref + now_iso_date.",
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
                "name": "get_recent_weeks_summary",
                "description": "Get a trend/evolution summary for recent weeks up to current week (default 4, configurable).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "weeks_count": {"type": "integer", "minimum": 1, "maximum": 24},
                        "now_iso_date": {"type": "string"},
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
                "description": "Signal that tool usage is complete and the orchestrator should run final answer synthesis.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
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
        temporal_ref = str(arguments.get("temporal_ref") or arguments.get("query") or "").strip()
        if not temporal_ref:
            return {
                "mode": "unresolved",
                "error": "missing_temporal_ref",
                "label": "",
            }
        return resolve_time_reference_tool(
            temporal_ref=temporal_ref,
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
        if temporal_resolution and temporal_resolution.get("resolved", {}).get("mode") == "unresolved":
            return {
                "error": "temporal_reference_unresolved",
                "temporal_resolution": temporal_resolution,
            }
        include_sessions = bool(arguments.get("include_sessions", False))
        return get_week_summary_tool(
            db,
            date_iso=date_iso,
            include_sessions=include_sessions,
            temporal_resolution=temporal_resolution,
            output_mode="text",
        )

    if name == "get_day_details":
        date_iso, temporal_resolution = _resolve_day_date_iso(
            date_iso=arguments.get("date_iso"),
            temporal_ref=arguments.get("temporal_ref"),
            now_iso_date=arguments.get("now_iso_date"),
            language=arguments.get("language"),
            resolver=time_resolver,
        )
        if temporal_resolution and temporal_resolution.get("resolved", {}).get("mode") == "unresolved":
            return {
                "error": "temporal_reference_unresolved",
                "temporal_resolution": temporal_resolution,
            }
        truncate_notes_chars = int(arguments.get("truncate_notes_chars", 220))
        return get_day_details_tool(
            db,
            date_iso=date_iso,
            truncate_notes_chars=truncate_notes_chars,
            temporal_resolution=temporal_resolution,
            output_mode="text",
        )

    if name == "get_session_details":
        session_id = int(arguments["session_id"])
        return get_session_details_tool(
            db,
            session_id=session_id,
            output_mode="text",
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
        if temporal_resolution and temporal_resolution.get("resolved", {}).get("mode") == "unresolved":
            return {
                "error": "temporal_reference_unresolved",
                "temporal_resolution": temporal_resolution,
            }
        return get_block_summary_tool(
            db,
            start_iso=start_iso,
            end_iso=end_iso,
            temporal_resolution=temporal_resolution,
            output_mode="text",
        )

    if name == "get_recent_weeks_summary":
        weeks_count = int(arguments.get("weeks_count", 4))
        now_iso_date = arguments.get("now_iso_date")
        return get_recent_weeks_summary_tool(
            db,
            weeks_count=weeks_count,
            now_iso_date=str(now_iso_date) if now_iso_date else None,
            output_mode="text",
        )

    if name == "submit_final_answer":
        return {
            "status": "ok",
        }

    raise ValueError(f"Unknown MCP tool '{name}'")
