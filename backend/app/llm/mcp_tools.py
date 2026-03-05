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


def _format_duration_hours(total_minutes: int) -> str:
    minutes = int(total_minutes or 0)
    hours = minutes // 60
    rem = minutes % 60
    if rem == 0:
        return f"{hours}h"
    return f"{hours}h{str(rem).zfill(2)}"


def _format_duration_seconds(total_seconds: int) -> str:
    return _format_duration_hours(int(round((int(total_seconds or 0)) / 60.0)))


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


def _natural_date_label(value: date, *, now_date: date | None = None) -> str:
    base = f"{_day_label(value)}, {value.strftime('%B')} {value.day}"
    if now_date is not None:
        if value == now_date:
            return f"{base} (today)"
        if value == (now_date - timedelta(days=1)):
            return f"{base} (yesterday)"
    return base


def _parse_now_date(now_iso_date: str | None) -> date:
    if now_iso_date:
        try:
            return date.fromisoformat(str(now_iso_date))
        except ValueError:
            pass
    return date.today()


def _is_same_iso_week(a: date, b: date) -> bool:
    a_iso = a.isocalendar()
    b_iso = b.isocalendar()
    return int(a_iso[0]) == int(b_iso[0]) and int(a_iso[1]) == int(b_iso[1])


def _to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_include_sessions_mode(include_sessions: Any) -> tuple[str, float | None]:
    if isinstance(include_sessions, bool):
        return ("all", None) if include_sessions else ("none", None)

    numeric = _to_float_or_none(include_sessions)
    if numeric is not None:
        return "threshold", max(0.0, numeric)

    return "none", None


def _fmt_metric(value: Any, digits: int = 0) -> str:
    numeric = _to_float_or_none(value)
    if numeric is None:
        return "n/a"
    return f"{numeric:.{max(0, int(digits))}f}"


def _fmt_percent_no_decimal(value: Any) -> str:
    numeric = _to_float_or_none(value)
    if numeric is None:
        return "n/a"
    return f"{int(round(numeric))}%"


def _acwr_ratio_to_percent(acwr_ratio: Any) -> int | None:
    numeric = _to_float_or_none(acwr_ratio)
    if numeric is None:
        return None
    return int(round(numeric * 100.0))


def _session_chronological_key(session: Any) -> tuple[str, str, int]:
    start_time = getattr(session, "start_time", None)
    if start_time is not None:
        try:
            start_time_key = start_time.isoformat()
        except Exception:
            start_time_key = ""
    else:
        start_time_key = ""
    return (
        str(getattr(session, "date", "")),
        start_time_key,
        int(getattr(session, "id", 0) or 0),
    )


def _fmt_distance_km(value: Any, *, session_type: str | None = None) -> str:
    numeric = _to_float_or_none(value)
    if numeric is None:
        return "n/a"
    normalized_type = str(session_type or "").strip().lower()
    if normalized_type == "bike":
        return f"{numeric:.0f}"
    if normalized_type in {"run", "trail"}:
        return f"{numeric:.1f}"
    return f"{numeric:.1f}"


def _fmt_elevation_m(value: Any) -> str:
    numeric = _to_float_or_none(value)
    if numeric is None:
        return "n/a"
    return f"{numeric:.0f}"


def _filter_salient_sessions(sessions: list[Any], include_sessions: Any) -> tuple[list[Any], dict[str, Any]]:
    mode, threshold = _parse_include_sessions_mode(include_sessions)
    if mode == "none":
        return [], {"mode": "none", "threshold": None, "returned": 0}

    if mode == "all":
        selected = list(sessions)
        selected.sort(key=_session_chronological_key)
        return selected, {"mode": "all", "threshold": None, "returned": len(selected)}

    selected = [s for s in sessions if (_to_float_or_none(getattr(s, "training_load", None)) or 0.0) >= float(threshold or 0.0)]
    selected.sort(key=_session_chronological_key)
    return selected, {"mode": "threshold", "threshold": float(threshold or 0.0), "returned": len(selected)}


def _compute_shape_snapshot(db: DBSession, *, on_date: date) -> dict[str, Any]:
    point = crud.get_latest_daily_training_load_on_or_before(db, on_date)
    if not point:
        return {"date": on_date.isoformat(), "shape_ctl": None, "acwr": None}

    shape_ctl = _to_float_or_none(getattr(point, "ctl", None))
    acwr = _acwr_ratio_to_percent(getattr(point, "acwr", None))
    return {
        "date": point.date.isoformat() if getattr(point, "date", None) else on_date.isoformat(),
        "shape_ctl": round(shape_ctl, 0) if shape_ctl is not None else None,
        "acwr": acwr,
    }


def _compute_avg_acwr(db: DBSession, *, start_date: date, end_date: date) -> float | None:
    points = crud.get_daily_training_load_by_date_range(db, start_date, end_date)
    acwrs = [_acwr_ratio_to_percent(getattr(point, "acwr", None)) for point in points]
    values = [value for value in acwrs if value is not None]
    if not values:
        return None
    return float(int(round(sum(values) / len(values))))


def _render_week_summary_text(payload: dict[str, Any]) -> str:
    totals = payload.get("totals", {})
    week_shape = payload.get("week_shape", {})
    lines = [
        f"{payload.get('date_start')} to {payload.get('date_end')} week summary:",
        (
            f"Week starts with Shape (CTL): {_fmt_metric(week_shape.get('shape_ctl'), 0)}. "
        ),
    ]
    current_week_day = payload.get("current_week_day")
    if current_week_day:
        lines.append(f"We are {current_week_day} of this week.")
    lines.append(f"Context: {totals.get('total_sessions', 0)} sessions.")

    plan = payload.get("plan") or {}
    lines.append(f"Week plan: {plan.get('description') or 'None'}")

    day_notes = payload.get("day_notes") or []
    if day_notes:
        lines.append("Day notes:")
        for note in day_notes:
            lines.append(f"- {note.get('date')}: {note.get('note') or ''}")

    sessions = payload.get("salient_sessions") or []
    salient_meta = payload.get("salient_sessions_meta") or {}
    if sessions:
        mode = salient_meta.get("mode")
        threshold = salient_meta.get("threshold")
        if mode == "threshold":
            lines.append(f"Salient sessions (TL ≥ {_fmt_metric(threshold, 0)}):")
        else:
            lines.append("Sessions:")
        for item in sessions:
            session_date = date.fromisoformat(str(item.get("date")))
            distance = item.get("distance_km")
            elev = item.get("elevation_gain_m")
            base = (
                f"{_day_label(session_date)} the {_ordinal_day(session_date)}, "
                f"{item.get('type')} TL {_fmt_metric(item.get('training_load'), 0)}, "
                f"{_format_duration_hours(int(item.get('moving_duration_minutes') or item.get('duration_minutes') or 0))}"
            )
            if distance:
                base += f", {_fmt_distance_km(distance, session_type=item.get('type'))} km"
            if elev:
                base += f", {_fmt_elevation_m(elev)} m+"
            lines.append(base)
            if item.get("notes"):
                lines.append(f"note: {item.get('notes')}")
    elif salient_meta.get("mode") == "threshold":
        lines.append(f"Salient sessions (TL ≥ {_fmt_metric(salient_meta.get('threshold'), 0)}): none")

    return "\n".join(lines)


def _render_day_details_text(payload: dict[str, Any]) -> str:
    totals = payload.get("totals", {})
    day_shape = payload.get("day_shape", {})
    lines = [
        f"{payload.get('date')} day details:",
        (
            f"Shape (CTL): {_fmt_metric(day_shape.get('shape_ctl'))}, Stress ratio (ACWR): {_fmt_percent_no_decimal(day_shape.get('acwr'))} "
        ),
        (
            f"{totals.get('total_sessions', 0)} sessions, "
            f"{_format_duration_hours(int(totals.get('total_duration_minutes') or 0))} total "
            f"({_format_duration_hours(int(totals.get('total_moving_minutes') or 0))} moving), "
            f"{_fmt_distance_km(totals.get('total_distance_km', 0), session_type='run')} km, {_fmt_elevation_m(totals.get('total_elevation_gain_m', 0))} m+"
        ),
    ]
    day_note = payload.get("day_note")
    if day_note:
        lines.append(f"Day note: {day_note}")

    current_day_label = payload.get("current_day_label")
    if current_day_label:
        lines.append(f"We are {current_day_label}.")

    sessions = payload.get("sessions") or []
    if sessions:
        lines.append("Sessions:")
        for item in sessions:
            base = f"- {item.get('type')} #{item.get('id')}: {_format_duration_hours(int(item.get('moving_duration_minutes') or item.get('duration_minutes') or 0))}"
            if item.get("distance_km"):
                base += f", {_fmt_distance_km(item.get('distance_km'), session_type=item.get('type'))} km"
            if item.get("elevation_gain_m"):
                base += f", {_fmt_elevation_m(item.get('elevation_gain_m'))} m+"
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
        lines.append(f"Distance: {_fmt_distance_km(payload.get('distance_km'), session_type=payload.get('type'))} km")
    if payload.get("elevation_gain_m"):
        lines.append(f"Elevation: {_fmt_elevation_m(payload.get('elevation_gain_m'))} m+")
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

    hr_zones = payload.get("hr_zones") or {}
    zone_values = [int(hr_zones.get(f"zone_{idx}_seconds") or 0) for idx in range(7)]
    if any(value > 0 for value in zone_values):
        lines.append("HR zones:")
        for idx, seconds in enumerate(zone_values):
            if seconds > 0:
                lines.append(f"- Z{idx}: {_format_duration_seconds(seconds)}")
    return "\n".join(lines)


def _render_block_summary_text(payload: dict[str, Any]) -> str:
    shape = payload.get("shape_summary", {})
    lines = [
        f"Block summary {payload.get('date_start')} to {payload.get('date_end')}:",
        (
            f"Shape (CTL) start→end: {_fmt_metric(shape.get('shape_ctl_start'))} → {_fmt_metric(shape.get('shape_ctl_end'))} "
            f"(Δ {_fmt_metric(shape.get('shape_change_ctl'))})."
        ),
        f"Average Stress ratio (ACWR) across block: {_fmt_percent_no_decimal(shape.get('avg_acwr'))}",
    ]

    current_week_day = payload.get("current_week_day")
    if current_week_day:
        lines.append(f"Current week is included in this block. We are {current_week_day} of the week.")

    lines.append(
        f"Context : {payload.get('total_sessions', 0)} sessions, {payload.get('active_training_days', 0)} active days."
    )

    salient_sessions = payload.get("salient_sessions") or []
    salient_meta = payload.get("salient_sessions_meta") or {}
    if salient_meta.get("mode") == "threshold":
        lines.append(f"Salient sessions (TL ≥ {_fmt_metric(salient_meta.get('threshold'), 0)}):")
    elif salient_meta.get("mode") == "all":
        lines.append("Sessions:")

    for item in salient_sessions:
        line = (
            f"- {item.get('date')} {item.get('type')} TL {_fmt_metric(item.get('training_load'), 0)}, "
            f"{_format_duration_hours(int(item.get('moving_duration_minutes') or item.get('duration_minutes') or 0))}"
        )
        if item.get("distance_km"):
            line += f", {_fmt_distance_km(item.get('distance_km'), session_type=item.get('type'))} km"
        if item.get("elevation_gain_m"):
            line += f", {_fmt_elevation_m(item.get('elevation_gain_m'))} m+"
        line += f" [session #{item.get('id')}]"
        lines.append(line)

    if salient_meta.get("mode") == "threshold" and not salient_sessions:
        lines.append("- none")

    weekly_breakdown = payload.get("weekly_breakdown") or []
    if weekly_breakdown:
        lines.append("Weekly Shape trend:")
        for item in weekly_breakdown:
            line = (
                f"- Week of {item.get('week_start')}: Shape (CTL) {_fmt_metric(item.get('shape_ctl'))}"
            )
            lines.append(line)

    return "\n".join(lines)


def _render_recent_weeks_summary_text(payload: dict[str, Any]) -> str:
    now_iso = payload.get("now_iso_date")
    weeks = payload.get("weeks") or []
    lines = [f"Recent {len(weeks)} weeks summary (today: {now_iso}):"]
    for item in reversed(weeks):
        line = (
            f"Week of {_month_day_label(date.fromisoformat(str(item.get('week_start'))))}: "
            f"Shape (CTL) {_fmt_metric(item.get('shape_ctl'), 0)}."
        )
        if item.get("is_current_week"):
            line += f" We are {item.get('current_week_day')} of this week."
        lines.append(line)

        lines.append(
            (
                f"Totals: {item.get('total_sessions', 0)} sessions, "
                f"{_fmt_distance_km(item.get('total_distance_km', 0), session_type='run')} km run/trail, "
                f"{_fmt_elevation_m(item.get('total_elevation_gain_m', 0))} m+, "
                f"TL {_fmt_metric(item.get('total_training_load'), 0)}"
            )
        )

        salient = item.get("salient_sessions") or []
        threshold = item.get("salient_threshold")
        if threshold is not None:
            lines.append(f"Salient sessions (TL ≥ {_fmt_metric(threshold, 0)}): {len(salient)}")
        elif item.get("salient_mode") == "all":
            lines.append(f"Sessions: {len(salient)}")

        for session in salient:
            session_line = (
                f"- {session.get('date')} {session.get('weekday')}: {session.get('type')} "
                f"TL {_fmt_metric(session.get('training_load'), 0)} [session #{session.get('session_id')}]"
            )
            lines.append(session_line)

        if threshold is not None and not salient:
            lines.append("- none")
    return "\n".join(lines)


def _render_salient_sessions_text(payload: dict[str, Any]) -> str:
    threshold = payload.get("effective_training_load_threshold")
    returned_count = int(payload.get("returned_count") or 0)
    lines = [
        (
            f"{returned_count} Salient sessions from {payload.get('date_start')} to {payload.get('date_end')} "
            f"with TL ≥ {_fmt_metric(threshold, 0)}"
        )
    ]

    shape = payload.get("shape_summary", {})
    lines.append(
        f"Shape (CTL) start→end: {_fmt_metric(shape.get('shape_ctl_start'))} → {_fmt_metric(shape.get('shape_ctl_end'))} | "
        f"Average Stress ratio (ACWR): {_fmt_percent_no_decimal(shape.get('avg_acwr'))}"
    )

    for item in payload.get("sessions") or []:
        line = (
            f"- {item.get('date')} {item.get('type')} TL {_fmt_metric(item.get('training_load'), 0)}, "
            f"{_format_duration_hours(int(item.get('moving_duration_minutes') or item.get('duration_minutes') or 0))}"
        )
        if item.get("distance_km"):
            line += f", {_fmt_distance_km(item.get('distance_km'), session_type=item.get('type'))} km"
        if item.get("elevation_gain_m"):
            line += f", {_fmt_elevation_m(item.get('elevation_gain_m'))} m+"
        line += f" [session #{item.get('id')}]"
        lines.append(line)

    return "\n".join(lines)


def _render_all_races_text(payload: dict[str, Any]) -> str:
    races = payload.get("races") or []
    lines = [f"All races: {len(races)}"]
    for item in races:
        line = (
            f"- {item.get('date')} | session #{item.get('session_id')} | {item.get('type')} | "
            f"distance {item.get('distance_km')} km | elevation {item.get('elevation_gain_m')} m+ | "
            f"moving {item.get('moving_time')} | elapsed {item.get('elapsed_time')} | TL {item.get('training_load')}"
        )
        if item.get("note"):
            line += f" | note: {item.get('note')}"
        if item.get("day_note"):
            line += f" | day note: {item.get('day_note')}"
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
    include_sessions: bool | int | float = False,
    now_iso_date: str | None = None,
    temporal_resolution: dict[str, Any] | None = None,
    output_mode: str = "text",
) -> dict[str, Any]:
    reference_date = date.fromisoformat(date_iso)
    now_date = _parse_now_date(now_iso_date)
    anchor_year, anchor_week = _iso_anchor_from_date(reference_date)
    start_date, end_date = _week_window(anchor_year, anchor_week)
    sessions = crud.get_sessions_by_date_range(db, start_date, end_date)
    day_notes = crud.get_day_notes_by_date_range(db, start_date, end_date)
    plan = crud.get_weekly_plan(db, anchor_year, anchor_week)

    total_duration = int(sum((s.duration_minutes or 0) for s in sessions))
    total_distance = round(sum((s.distance_km or 0) for s in sessions if s.type in ["run", "trail"]), 1)
    total_elevation = int(sum((s.elevation_gain_m or 0) for s in sessions if s.type in ["run", "trail", "hike"]))
    week_shape = _compute_shape_snapshot(db, on_date=start_date)

    payload: dict[str, Any] = {
        "date_start": start_date.isoformat(),
        "date_end": end_date.isoformat(),
        "week_shape": week_shape,
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

    if _is_same_iso_week(reference_date, now_date):
        payload["current_week_day"] = _day_label(now_date)

    selected_sessions, salient_meta = _filter_salient_sessions(sessions, include_sessions)
    payload["salient_sessions_meta"] = salient_meta
    if selected_sessions:
        payload["salient_sessions"] = [
            {
                "date": s.date.isoformat(),
                "id": s.id,
                "type": s.type,
                "training_load": round(_to_float_or_none(s.training_load) or 0.0, 0),
                "moving_duration_minutes": s.moving_duration_minutes,
                "duration_minutes": s.duration_minutes,
                "distance_km": s.distance_km,
                "elevation_gain_m": s.elevation_gain_m,
                "notes": _truncate_text(s.notes, max_chars=140),
            }
            for s in selected_sessions
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
    now_iso_date: str | None = None,
    temporal_resolution: dict[str, Any] | None = None,
    output_mode: str = "text",
) -> dict[str, Any]:
    target_date = date.fromisoformat(date_iso)
    now_date = _parse_now_date(now_iso_date)
    sessions = crud.get_sessions_by_date_range(db, target_date, target_date)
    day_note = crud.get_day_note(db, target_date)

    payload = {
        "date": target_date.isoformat(),
        "day_shape": _compute_shape_snapshot(db, on_date=target_date),
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

    if target_date == now_date:
        payload["current_day_label"] = _day_label(now_date)

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

    hr_zone_map = crud.get_session_hr_zone_time_map(db, [int(session.id)])
    hr_zones = hr_zone_map.get(int(session.id)) or {
        "zone_0_seconds": 0,
        "zone_1_seconds": 0,
        "zone_2_seconds": 0,
        "zone_3_seconds": 0,
        "zone_4_seconds": 0,
        "zone_5_seconds": 0,
        "zone_6_seconds": 0,
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
        "hr_zones": hr_zones,
    }

    if output_mode == "json":
        return payload

    return {"text": _render_session_details_text(payload)}


def get_block_summary_tool(
    db: DBSession,
    *,
    start_iso: str,
    end_iso: str,
    include_sessions: bool | int | float = 150,
    now_iso_date: str | None = None,
    temporal_resolution: dict[str, Any] | None = None,
    output_mode: str = "text",
) -> dict[str, Any]:
    start_date = date.fromisoformat(start_iso)
    end_date = date.fromisoformat(end_iso)
    now_date = _parse_now_date(now_iso_date)
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    sessions = crud.get_sessions_by_date_range(db, start_date, end_date)
    day_notes = crud.get_day_notes_by_date_range(db, start_date, end_date)

    number_of_days = (end_date - start_date).days + 1
    active_training_days = len({s.date for s in sessions})
    noted_days = len({n.date for n in day_notes})

    start_shape = _compute_shape_snapshot(db, on_date=start_date)
    end_shape = _compute_shape_snapshot(db, on_date=end_date)
    avg_acwr = _compute_avg_acwr(db, start_date=start_date, end_date=end_date)

    first_week_start = start_date - timedelta(days=start_date.weekday())
    last_week_start = end_date - timedelta(days=end_date.weekday())
    weekly_breakdown: list[dict[str, Any]] = []
    cursor = first_week_start
    while cursor <= last_week_start:
        window_start = max(cursor, start_date)
        window_end = min(cursor + timedelta(days=6), end_date)
        week_sessions = [s for s in sessions if window_start <= s.date <= window_end]
        week_shape = _compute_shape_snapshot(db, on_date=window_start)

        weekly_breakdown.append(
            {
                "week_start": cursor.isoformat(),
                "week_end": (cursor + timedelta(days=6)).isoformat(),
                "shape_ctl": week_shape.get("shape_ctl"),
            }
        )
        cursor = cursor + timedelta(days=7)

    selected_sessions, salient_meta = _filter_salient_sessions(sessions, include_sessions)

    payload = {
        "date_start": start_date.isoformat(),
        "date_end": end_date.isoformat(),
        "shape_summary": {
            "shape_ctl_start": start_shape.get("shape_ctl"),
            "shape_ctl_end": end_shape.get("shape_ctl"),
            "shape_change_ctl": (
                round(float(end_shape.get("shape_ctl")) - float(start_shape.get("shape_ctl")), 2)
                if start_shape.get("shape_ctl") is not None and end_shape.get("shape_ctl") is not None
                else None
            ),
            "avg_acwr": avg_acwr,
        },
        "number_of_days": number_of_days,
        "active_training_days": active_training_days,
        "days_with_notes": noted_days,
        "total_sessions": len(sessions),
        "weekly_breakdown": weekly_breakdown,
        "salient_sessions_meta": salient_meta,
        "salient_sessions": [
            {
                "id": s.id,
                "date": s.date.isoformat(),
                "type": s.type,
                "training_load": round(_to_float_or_none(s.training_load) or 0.0, 0),
                "moving_duration_minutes": s.moving_duration_minutes,
                "duration_minutes": s.duration_minutes,
                "distance_km": s.distance_km,
                "elevation_gain_m": s.elevation_gain_m,
            }
            for s in selected_sessions
        ],
    }

    current_week_start = now_date - timedelta(days=now_date.weekday())
    current_week_end = current_week_start + timedelta(days=6)
    if start_date <= current_week_end and end_date >= current_week_start:
        payload["current_week_day"] = _day_label(now_date)

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
    include_sessions: bool | int | float = 150,
    output_mode: str = "text",
) -> dict[str, Any]:
    count = max(1, min(int(weeks_count), 24))
    now_date = _parse_now_date(now_iso_date)
    current_week_start = now_date - timedelta(days=now_date.weekday())

    weeks: list[dict[str, Any]] = []
    for offset in range(count):
        week_start = current_week_start - timedelta(days=7 * offset)
        week_end = week_start + timedelta(days=6)
        effective_end = min(week_end, now_date)
        week_sessions = crud.get_sessions_by_date_range(db, week_start, effective_end)
        week_shape = _compute_shape_snapshot(db, on_date=week_start)
        selected_sessions, salient_meta = _filter_salient_sessions(week_sessions, include_sessions)
        threshold = salient_meta.get("threshold") if salient_meta.get("mode") == "threshold" else None
        total_distance = round(sum((s.distance_km or 0) for s in week_sessions if s.type in ["run", "trail"]), 1)
        total_elevation = int(sum((s.elevation_gain_m or 0) for s in week_sessions if s.type in ["run", "trail", "hike"]))
        total_training_load = round(sum((_to_float_or_none(s.training_load) or 0.0) for s in week_sessions), 0)

        weeks.append(
            {
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "effective_end": effective_end.isoformat(),
                "shape_ctl": week_shape.get("shape_ctl"),
                "is_current_week": week_start == current_week_start,
                "current_week_day": _day_label(now_date) if week_start == current_week_start else None,
                "salient_mode": salient_meta.get("mode"),
                "salient_threshold": threshold,
                "total_sessions": len(week_sessions),
                "total_distance_km": total_distance,
                "total_elevation_gain_m": total_elevation,
                "total_training_load": total_training_load,
                "salient_sessions": [
                    {
                        "session_id": s.id,
                        "date": s.date.isoformat(),
                        "weekday": _day_label(s.date),
                        "type": s.type,
                        "training_load": round(_to_float_or_none(s.training_load) or 0.0, 0),
                    }
                    for s in selected_sessions
                ],
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


def get_salient_sessions_tool(
    db: DBSession,
    *,
    start_iso: str,
    end_iso: str,
    training_load_threshold: float = 150.0,
    limit: int = 50,
    temporal_resolution: dict[str, Any] | None = None,
    output_mode: str = "text",
) -> dict[str, Any]:
    start_date = date.fromisoformat(start_iso)
    end_date = date.fromisoformat(end_iso)
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    threshold = max(0.0, float(training_load_threshold))
    sessions = crud.get_sessions_by_date_range(db, start_date, end_date)
    salient = [s for s in sessions if (_to_float_or_none(s.training_load) or 0.0) >= threshold]
    salient.sort(key=lambda s: (_to_float_or_none(s.training_load) or 0.0, s.date, s.id or 0), reverse=True)

    effective_limit = max(1, min(int(limit), 200))
    capped = salient[:effective_limit]
    is_truncated = len(salient) > len(capped)
    effective_threshold = threshold
    if capped and is_truncated:
        effective_threshold = min((_to_float_or_none(s.training_load) or 0.0) for s in capped)

    capped_display = sorted(
        capped,
        key=lambda s: (s.date, s.start_time.isoformat() if s.start_time else "", s.id or 0),
    )
    start_shape = _compute_shape_snapshot(db, on_date=start_date)
    end_shape = _compute_shape_snapshot(db, on_date=end_date)
    avg_acwr = _compute_avg_acwr(db, start_date=start_date, end_date=end_date)

    payload = {
        "date_start": start_date.isoformat(),
        "date_end": end_date.isoformat(),
        "training_load_threshold": round(threshold, 0),
        "effective_training_load_threshold": round(effective_threshold, 0),
        "total": len(salient),
        "returned_count": len(capped_display),
        "limit_reached": is_truncated,
        "shape_summary": {
            "shape_ctl_start": start_shape.get("shape_ctl"),
            "shape_ctl_end": end_shape.get("shape_ctl"),
            "avg_acwr": avg_acwr,
        },
        "sessions": [
            {
                "id": s.id,
                "date": s.date.isoformat(),
                "type": s.type,
                "training_load": round(_to_float_or_none(s.training_load) or 0.0, 0),
                "moving_duration_minutes": s.moving_duration_minutes,
                "duration_minutes": s.duration_minutes,
                "distance_km": s.distance_km,
                "elevation_gain_m": s.elevation_gain_m,
            }
            for s in capped_display
        ],
    }

    if temporal_resolution:
        payload["temporal_resolution"] = temporal_resolution

    if output_mode == "json":
        return payload

    return {"text": _render_salient_sessions_text(payload)}


def get_all_races_tool(
    db: DBSession,
    *,
    output_mode: str = "text",
) -> dict[str, Any]:
    races = crud.get_race_sessions(db)
    day_notes_map = {
        str(item.date): item.note
        for item in crud.get_day_notes_by_date_range(
            db,
            min((race.date for race in races), default=date.today()),
            max((race.date for race in races), default=date.today()),
        )
    }

    payload = {
        "races": [
            {
                "date": race.date.isoformat(),
                "session_id": race.id,
                "type": race.type,
                "distance_km": _fmt_distance_km(race.distance_km, session_type=race.type),
                "elevation_gain_m": _fmt_elevation_m(race.elevation_gain_m),
                "moving_time": _format_duration_hours(int(race.moving_duration_minutes or race.duration_minutes or 0)),
                "elapsed_time": _format_duration_hours(int(race.elapsed_duration_minutes or race.duration_minutes or 0)),
                "training_load": _fmt_metric(race.training_load, 0),
                "note": _truncate_text(race.notes, max_chars=220),
                "day_note": day_notes_map.get(str(race.date)) or None,
            }
            for race in races
        ]
    }

    if output_mode == "json":
        return payload

    return {"text": _render_all_races_text(payload)}


def get_recent_context_tool(
    db: DBSession,
    *,
    now_iso_date: str | None = None,
    output_mode: str = "text",
) -> dict[str, Any]:
    now_date = _parse_now_date(now_iso_date)
    week_year, week_number = _iso_anchor_from_date(now_date)
    week_start, week_end = _week_window(week_year, week_number)
    week_plan = crud.get_weekly_plan(db, week_year, week_number)

    today_shape = _compute_shape_snapshot(db, on_date=now_date)

    range_7_start = now_date - timedelta(days=6)
    range_7_end = now_date
    recent_points = crud.get_daily_training_load_by_date_range(db, range_7_start, range_7_end)
    total_charge_last_7_days = round(sum((_to_float_or_none(getattr(point, "load", None)) or 0.0) for point in recent_points), 0)

    focus_start = now_date - timedelta(days=6)
    focus_end = now_date - timedelta(days=2)
    focus_sessions_all = crud.get_sessions_by_date_range(db, focus_start, focus_end) if focus_start <= focus_end else []
    focus_sessions: list[Any] = []
    for session in focus_sessions_all:
        is_strength = str(getattr(session, "type", "")).strip().lower() == "strength"
        tl_value = _to_float_or_none(getattr(session, "training_load", None)) or 0.0
        if is_strength or tl_value > 150.0:
            focus_sessions.append(session)
    focus_sessions.sort(key=_session_chronological_key)

    recent_workouts_start = now_date - timedelta(days=1)
    recent_workouts = crud.get_sessions_by_date_range(db, recent_workouts_start, now_date)
    recent_workouts.sort(key=_session_chronological_key)

    note_start = focus_start
    note_end = now_date
    notes = crud.get_day_notes_by_date_range(db, note_start, note_end) if note_start <= note_end else []
    day_notes_map = {item.date.isoformat(): item.note for item in notes}

    payload = {
        "current_week_plan": {
            "year": week_year,
            "week_number": week_number,
            "date_start": week_start.isoformat(),
            "date_end": week_end.isoformat(),
            "description": (week_plan.description if week_plan else None),
            "target_distance_km": (week_plan.target_distance_km if week_plan else None),
            "target_sessions": (week_plan.target_sessions if week_plan else None),
            "tags": (week_plan.tags if week_plan else None),
        },
        "today": {
            "date": now_date.isoformat(),
            "shape_ctl": today_shape.get("shape_ctl"),
            "acwr": today_shape.get("acwr"),
            "total_charge_last_7_days": total_charge_last_7_days,
            "charge_window": {
                "start": range_7_start.isoformat(),
                "end": range_7_end.isoformat(),
            },
        },
        "focus_workouts": {
            "window_start": focus_start.isoformat(),
            "window_end": focus_end.isoformat(),
            "items": [
                {
                    "id": session.id,
                    "date": session.date.isoformat(),
                    "type": session.type,
                    "training_load": round(_to_float_or_none(session.training_load) or 0.0, 0),
                    "moving_duration_minutes": session.moving_duration_minutes,
                    "duration_minutes": session.duration_minutes,
                    "distance_km": session.distance_km,
                    "elevation_gain_m": session.elevation_gain_m,
                    "notes": _truncate_text(session.notes, max_chars=140),
                    "day_note": _truncate_text(day_notes_map.get(session.date.isoformat()), max_chars=160),
                }
                for session in focus_sessions
            ],
        },
        "latest_workouts": {
            "window_start": recent_workouts_start.isoformat(),
            "window_end": now_date.isoformat(),
            "items": [
                {
                    "id": session.id,
                    "date": session.date.isoformat(),
                    "type": session.type,
                    "training_load": round(_to_float_or_none(session.training_load) or 0.0, 0),
                    "moving_duration_minutes": session.moving_duration_minutes,
                    "duration_minutes": session.duration_minutes,
                    "distance_km": session.distance_km,
                    "elevation_gain_m": session.elevation_gain_m,
                    "notes": _truncate_text(session.notes, max_chars=140),
                }
                for session in recent_workouts
            ],
        },
    }

    if output_mode == "json":
        return payload

    lines: list[str] = []
    lines.append("Current week plan:")
    plan_desc = payload["current_week_plan"].get("description")
    lines.append(f"- {plan_desc if plan_desc else 'None'}")

    today = payload["today"]
    lines.append(
        "Today summary: "
        f"Shape (CTL) {_fmt_metric(today.get('shape_ctl'), 0)}, "
        f"Trend (ACWR) {_fmt_percent_no_decimal(today.get('acwr'))}, "
        f"total charge last 7 days {_fmt_metric(today.get('total_charge_last_7_days'), 0)}"
    )

    lines.append("Strength workouts and >150 TL workouts (today-6 to today-2), chronological:")
    focus_items = payload["focus_workouts"].get("items") or []
    if not focus_items:
        lines.append("- none")
    else:
        for item in focus_items:
            item_date_label = str(item.get("date") or "")
            try:
                item_date_label = _natural_date_label(date.fromisoformat(str(item.get("date"))), now_date=now_date)
            except ValueError:
                pass
            line = (
                f"- {item_date_label}: {item.get('type')} TL {_fmt_metric(item.get('training_load'), 0)} "
                f"({_format_duration_hours(int(item.get('moving_duration_minutes') or item.get('duration_minutes') or 0))})"
            )
            lines.append(line)
            if item.get("day_note"):
                lines.append(f"  day note: {item.get('day_note')}")
            if item.get("notes"):
                lines.append(f"  note: {item.get('notes')}")

    lines.append("All workouts from today-1 and today:")
    latest_items = payload["latest_workouts"].get("items") or []
    has_today_workout = False
    if not latest_items:
        lines.append(f"- {_natural_date_label(now_date, now_date=now_date)}: nothing yet")
    else:
        for item in latest_items:
            item_date_label = str(item.get("date") or "")
            item_date_obj: date | None = None
            try:
                item_date_obj = date.fromisoformat(str(item.get("date")))
                item_date_label = _natural_date_label(item_date_obj, now_date=now_date)
            except ValueError:
                item_date_obj = None

            if item_date_obj == now_date:
                has_today_workout = True

            line = (
                f"- {item_date_label}: {item.get('type')} TL {_fmt_metric(item.get('training_load'), 0)} "
                f"({_format_duration_hours(int(item.get('moving_duration_minutes') or item.get('duration_minutes') or 0))})"
            )
            lines.append(line)
            if item.get("notes"):
                lines.append(f"  note: {item.get('notes')}")

        if not has_today_workout:
            lines.append(f"- {_natural_date_label(now_date, now_date=now_date)}: nothing yet")

    return {"text": "\n".join(lines)}


def get_mcp_tools_schema() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "get_week_summary",
                "description": "Get a compact week summary of the week (Monday to Sunday) that includes the given date. include_sessions defaults accepts false (none, default), true (all), or a numeric TL threshold.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date_iso": {"type": "string"},
                        "include_sessions": {
                            "anyOf": [
                                {"type": "boolean"},
                                {"type": "number", "minimum": 0}
                            ]
                        },
                    },
                    "required": ["date_iso"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_day_details",
                "description": "Get a detailed summary for one day, including per-session details with moving time and truncated notes.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date_iso": {"type": "string"},
                        "truncate_notes_chars": {"type": "integer", "minimum": 40, "maximum": 1000},
                    },
                    "required": ["date_iso"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_session_details",
                "description": "Get a detailed summary for one session by session id.",
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
                "description": "Get a compact block summary with Shape evolution (start/end CTL), average ACWR, and optional salient sessions. include_sessions accepts false (none, default), true (all), or a numeric TL threshold.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_iso": {"type": "string"},
                        "end_iso": {"type": "string"},
                        "include_sessions": {
                            "anyOf": [
                                {"type": "boolean"},
                                {"type": "number", "minimum": 0}
                            ]
                        },
                    },
                    "required": ["start_iso", "end_iso"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_recent_weeks_summary",
                "description": "Get recent weeks from oldest to newest with weekly Shape (CTL) and optional salient sessions. include_sessions accepts false (none), true (all), or a numeric TL threshold.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "weeks_count": {"type": "integer", "minimum": 1, "maximum": 24},
                        "include_sessions": {
                            "anyOf": [
                                {"type": "boolean"},
                                {"type": "number", "minimum": 0}
                            ]
                        },
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_salient_sessions",
                "description": "Get salient sessions in a period: sessions in the date range with training load at or above training_load_threshold (default 150), capped by limit (default 50).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_iso": {"type": "string"},
                        "end_iso": {"type": "string"},
                        "training_load_threshold": {"type": "number", "minimum": 0, "default": 150},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                    },
                    "required": ["start_iso", "end_iso"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_all_races",
                "description": "Get all sessions marked as race.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_recent_context",
                "description": "Get recent context: week plan, shape, sessions of the last few days to estimate current fatigue or freshness.",
                "parameters": {
                    "type": "object",
                    "properties": {},
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
        include_sessions = arguments.get("include_sessions", False)
        return get_week_summary_tool(
            db,
            date_iso=date_iso,
            include_sessions=include_sessions,
            now_iso_date=str(arguments.get("now_iso_date") or "") or None,
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
            now_iso_date=str(arguments.get("now_iso_date") or "") or None,
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
            include_sessions=arguments.get("include_sessions", 150),
            now_iso_date=str(arguments.get("now_iso_date") or "") or None,
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
            include_sessions=arguments.get("include_sessions", 150),
            output_mode="text",
        )

    if name == "get_salient_sessions":
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
        return get_salient_sessions_tool(
            db,
            start_iso=start_iso,
            end_iso=end_iso,
            training_load_threshold=float(arguments.get("training_load_threshold", 150.0)),
            limit=int(arguments.get("limit", 50)),
            temporal_resolution=temporal_resolution,
            output_mode="text",
        )

    if name == "get_all_races":
        return get_all_races_tool(
            db,
            output_mode="text",
        )

    if name == "get_recent_context":
        now_iso_date = arguments.get("now_iso_date")
        return get_recent_context_tool(
            db,
            now_iso_date=str(now_iso_date) if now_iso_date else None,
            output_mode="text",
        )

    if name == "submit_final_answer":
        return {
            "status": "ok",
        }

    raise ValueError(f"Unknown MCP tool '{name}'")
