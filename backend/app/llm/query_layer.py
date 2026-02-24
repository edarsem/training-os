from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session as DBSession

from app.crud import crud
from app.schemas import schemas


@dataclass(frozen=True)
class ContextWindow:
    start_date: date
    end_date: date
    anchor_year: int
    anchor_week: int


def _week_window(year: int, week_number: int) -> tuple[date, date]:
    start = date.fromisocalendar(year, week_number, 1)
    return start, start + timedelta(days=6)


def _get_iso_anchor(target: date) -> tuple[int, int]:
    iso = target.isocalendar()
    return int(iso[0]), int(iso[1])


def resolve_context_window(
    *,
    anchor_year: int | None,
    anchor_week: int | None,
    date_start: date | None,
    date_end: date | None,
) -> ContextWindow:
    if anchor_year is not None and anchor_week is not None:
        start, end = _week_window(anchor_year, anchor_week)
        return ContextWindow(start_date=start, end_date=end, anchor_year=anchor_year, anchor_week=anchor_week)

    if date_start is not None and date_end is not None:
        start = min(date_start, date_end)
        end = max(date_start, date_end)
        anchor_year, anchor_week = _get_iso_anchor(end)
        return ContextWindow(start_date=start, end_date=end, anchor_year=anchor_year, anchor_week=anchor_week)

    today = date.today()
    anchor_year, anchor_week = _get_iso_anchor(today)
    start, end = _week_window(anchor_year, anchor_week)
    return ContextWindow(start_date=start, end_date=end, anchor_year=anchor_year, anchor_week=anchor_week)


def _session_to_dict(session: Any) -> dict[str, Any]:
    return {
        "id": session.id,
        "date": session.date.isoformat(),
        "start_time": session.start_time.isoformat() if session.start_time else None,
        "type": session.type,
        "duration_minutes": session.duration_minutes,
        "moving_duration_minutes": session.moving_duration_minutes,
        "elapsed_duration_minutes": session.elapsed_duration_minutes,
        "distance_km": session.distance_km,
        "elevation_gain_m": session.elevation_gain_m,
        "average_pace_min_per_km": session.average_pace_min_per_km,
        "average_heart_rate_bpm": session.average_heart_rate_bpm,
        "max_heart_rate_bpm": session.max_heart_rate_bpm,
        "perceived_intensity": session.perceived_intensity,
        "has_notes": bool(session.notes and session.notes.strip()),
        "notes": session.notes,
        "external_id": session.external_id,
    }


def _compute_totals(sessions: list[Any]) -> dict[str, Any]:
    total_duration_minutes = int(sum((s.duration_minutes or 0) for s in sessions))
    total_distance_km = round(sum((s.distance_km or 0) for s in sessions if s.type in {"run", "trail"}), 3)
    total_elevation_gain_m = int(sum((s.elevation_gain_m or 0) for s in sessions if s.type in {"run", "trail", "hike"}))
    total_sessions = len(sessions)
    return {
        "total_sessions": total_sessions,
        "total_duration_minutes": total_duration_minutes,
        "total_distance_km": total_distance_km,
        "total_elevation_gain_m": total_elevation_gain_m,
    }


def _plan_to_dict(plan: Any) -> dict[str, Any] | None:
    if not plan:
        return None
    return {
        "year": plan.year,
        "week_number": plan.week_number,
        "description": plan.description,
        "target_distance_km": plan.target_distance_km,
        "target_sessions": plan.target_sessions,
        "tags": plan.tags,
    }


def _plan_vs_actual(plan: Any, sessions: list[Any]) -> dict[str, Any]:
    totals = _compute_totals(sessions)
    out: dict[str, Any] = {
        "actual": {
            "sessions": totals["total_sessions"],
            "distance_km": totals["total_distance_km"],
            "duration_minutes": totals["total_duration_minutes"],
            "elevation_gain_m": totals["total_elevation_gain_m"],
        },
        "targets": {
            "sessions": None,
            "distance_km": None,
        },
        "delta": {
            "sessions": None,
            "distance_km": None,
        },
    }

    if plan:
        out["targets"]["sessions"] = plan.target_sessions
        out["targets"]["distance_km"] = plan.target_distance_km
        if plan.target_sessions is not None:
            out["delta"]["sessions"] = totals["total_sessions"] - plan.target_sessions
        if plan.target_distance_km is not None:
            out["delta"]["distance_km"] = round(totals["total_distance_km"] - float(plan.target_distance_km), 3)

    return out


def _salient_sessions(
    sessions: list[Any],
    *,
    distance_threshold_km: float,
    duration_threshold_min: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for session in sessions:
        has_note = bool(session.notes and session.notes.strip())
        long_distance = (session.distance_km or 0) >= distance_threshold_km
        long_duration = (session.duration_minutes or 0) >= duration_threshold_min
        hard_intensity = (session.perceived_intensity or 0) >= 8

        if has_note or long_distance or long_duration or hard_intensity:
            reasons = []
            if has_note:
                reasons.append("has_note")
            if long_distance:
                reasons.append("long_distance")
            if long_duration:
                reasons.append("long_duration")
            if hard_intensity:
                reasons.append("high_intensity")

            item = _session_to_dict(session)
            item["salient_reasons"] = reasons
            out.append(item)

    return out


def _week_range(start_date: date, end_date: date) -> list[tuple[int, int, date, date]]:
    current = start_date
    seen: set[tuple[int, int]] = set()
    weeks: list[tuple[int, int, date, date]] = []

    while current <= end_date:
        year, week = _get_iso_anchor(current)
        if (year, week) not in seen:
            week_start, week_end = _week_window(year, week)
            clipped_start = max(week_start, start_date)
            clipped_end = min(week_end, end_date)
            weeks.append((year, week, clipped_start, clipped_end))
            seen.add((year, week))
        current += timedelta(days=1)

    weeks.sort(key=lambda item: (item[0], item[1]))
    return weeks


class TrainingDataQueryService:
    def __init__(self, db: DBSession):
        self.db = db

    def build_context(self, request: schemas.LLMInterpretRequest) -> dict[str, Any]:
        window = resolve_context_window(
            anchor_year=request.anchor_year,
            anchor_week=request.anchor_week,
            date_start=request.date_start,
            date_end=request.date_end,
        )

        sessions = crud.get_sessions_by_date_range(self.db, window.start_date, window.end_date)
        day_notes = crud.get_day_notes_by_date_range(self.db, window.start_date, window.end_date)

        levels_payload: dict[str, Any] = {}

        levels = [level.value for level in request.levels]

        if schemas.LLMContextLevel.session.value in levels:
            session_items = [_session_to_dict(session) for session in sessions]
            levels_payload[schemas.LLMContextLevel.session.value] = {
                "count": len(session_items),
                "items": session_items[: request.max_sessions_per_level],
            }

        if schemas.LLMContextLevel.day.value in levels:
            notes_by_date = {n.date: n.note for n in day_notes}
            per_day: dict[date, list[Any]] = {}
            for session in sessions:
                per_day.setdefault(session.date, []).append(session)

            day_items: list[dict[str, Any]] = []
            current = window.start_date
            while current <= window.end_date:
                day_sessions = per_day.get(current, [])
                totals = _compute_totals(day_sessions)
                day_items.append(
                    {
                        "date": current.isoformat(),
                        "day_note": notes_by_date.get(current),
                        "sessions": [_session_to_dict(item) for item in day_sessions],
                        "totals": totals,
                    }
                )
                current += timedelta(days=1)

            levels_payload[schemas.LLMContextLevel.day.value] = {
                "count": len(day_items),
                "items": day_items,
            }

        if schemas.LLMContextLevel.week.value in levels:
            week_summaries: list[dict[str, Any]] = []
            for year, week_number, week_start, week_end in _week_range(window.start_date, window.end_date):
                week_sessions = crud.get_sessions_by_date_range(self.db, week_start, week_end)
                week_plan = crud.get_weekly_plan(self.db, year, week_number)
                week_summaries.append(
                    {
                        "year": year,
                        "week_number": week_number,
                        "date_start": week_start.isoformat(),
                        "date_end": week_end.isoformat(),
                        "plan": _plan_to_dict(week_plan),
                        "totals": _compute_totals(week_sessions),
                        "plan_vs_actual": _plan_vs_actual(week_plan, week_sessions),
                        "sessions_count": len(week_sessions),
                    }
                )

            levels_payload[schemas.LLMContextLevel.week.value] = {
                "count": len(week_summaries),
                "items": week_summaries,
            }

        if schemas.LLMContextLevel.multi_week.value in levels:
            anchor_end = date.fromisocalendar(window.anchor_year, window.anchor_week, 7)
            anchor_start = anchor_end - timedelta(weeks=max(1, request.multi_week_count) - 1)
            multi_week_start = get_start_of_iso_week(anchor_start)
            multi_week_end = anchor_end

            weekly_items: list[dict[str, Any]] = []
            for year, week_number, week_start, week_end in _week_range(multi_week_start, multi_week_end):
                week_sessions = crud.get_sessions_by_date_range(self.db, week_start, week_end)
                week_plan = crud.get_weekly_plan(self.db, year, week_number)
                weekly_items.append(
                    {
                        "year": year,
                        "week_number": week_number,
                        "totals": _compute_totals(week_sessions),
                        "plan_vs_actual": _plan_vs_actual(week_plan, week_sessions),
                    }
                )

            weekly_items.sort(key=lambda item: (item["year"], item["week_number"]))
            levels_payload[schemas.LLMContextLevel.multi_week.value] = {
                "count": len(weekly_items),
                "window": {
                    "date_start": multi_week_start.isoformat(),
                    "date_end": multi_week_end.isoformat(),
                },
                "items": weekly_items,
            }

        if schemas.LLMContextLevel.block.value in levels:
            notes_count = len([note for note in day_notes if note.note and note.note.strip()])
            block_payload: dict[str, Any] = {
                "date_start": window.start_date.isoformat(),
                "date_end": window.end_date.isoformat(),
                "totals": _compute_totals(sessions),
                "day_notes_count": notes_count,
            }

            week_distance_series = []
            for year, week_number, week_start, week_end in _week_range(window.start_date, window.end_date):
                week_sessions = crud.get_sessions_by_date_range(self.db, week_start, week_end)
                totals = _compute_totals(week_sessions)
                week_distance_series.append(
                    {
                        "year": year,
                        "week_number": week_number,
                        "distance_km": totals["total_distance_km"],
                        "duration_minutes": totals["total_duration_minutes"],
                    }
                )
            block_payload["weekly_trend"] = week_distance_series
            levels_payload[schemas.LLMContextLevel.block.value] = block_payload

        salient: list[dict[str, Any]] = []
        if request.include_salient_sessions:
            salient = _salient_sessions(
                sessions,
                distance_threshold_km=request.salient_distance_km_threshold,
                duration_threshold_min=request.salient_duration_minutes_threshold,
            )

        context = {
            "meta": {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "window": {
                    "date_start": window.start_date.isoformat(),
                    "date_end": window.end_date.isoformat(),
                    "anchor_year": window.anchor_year,
                    "anchor_week": window.anchor_week,
                },
                "levels": levels,
            },
            "levels": levels_payload,
            "salient_sessions": salient,
            "salient_sessions_count": len(salient),
        }

        return context


def get_start_of_iso_week(value: date) -> date:
    return value - timedelta(days=value.isoweekday() - 1)
