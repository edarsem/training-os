from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine, run_sqlite_schema_updates
from app.core.strava import StravaAPIError, StravaClient
from app.core.training_load_defaults import softplus4_training_load_per_hour
from app.models import models


@dataclass
class RecomputeStats:
    stream_files_seen: int = 0
    sessions_updated: int = 0
    streams_missing_hr: int = 0
    streams_missing_session: int = 0
    streams_parse_error: int = 0


def _build_activity_external_id_index(*, per_page: int = 100, max_pages: int = 100) -> dict[int, str]:
    client = StravaClient()
    out: dict[int, str] = {}

    for page in range(1, max_pages + 1):
        page_data = client.get_activities_page(page=page, per_page=per_page)
        activities = page_data.get("activities", [])
        if not activities:
            break

        for activity in activities:
            activity_id_raw = activity.get("id")
            if activity_id_raw is None:
                continue
            try:
                activity_id = int(activity_id_raw)
            except (TypeError, ValueError):
                continue

            external_source_id = str(activity.get("external_id") or "").strip()
            if external_source_id:
                out[activity_id] = external_source_id
            else:
                out[activity_id] = f"strava:{activity_id}"

        if len(activities) < per_page:
            break

    return out


def _resolve_session_for_activity_id(
    db,
    activity_id: int,
    activity_external_id_index: dict[int, str] | None = None,
) -> models.Session | None:
    exact_external_id = f"strava:{activity_id}"
    exact = db.query(models.Session).filter(models.Session.external_id == exact_external_id).first()
    if exact is not None:
        return exact

    like_matches = (
        db.query(models.Session)
        .filter(models.Session.external_id.like(f"%{activity_id}%"))
        .order_by(models.Session.id.desc())
        .all()
    )
    if not like_matches:
        if activity_external_id_index is None:
            return None

        mapped_external_id = activity_external_id_index.get(int(activity_id))
        if not mapped_external_id:
            return None

        mapped = db.query(models.Session).filter(models.Session.external_id == mapped_external_id).first()
        if mapped is not None:
            return mapped

        mapped_like = (
            db.query(models.Session)
            .filter(models.Session.external_id.like(f"%{mapped_external_id}%"))
            .order_by(models.Session.id.desc())
            .first()
        )
        return mapped_like
    return like_matches[0]


def _compute_training_load_from_stream_payload(streams_payload: dict, max_hr_bpm: float) -> float | None:
    heartrate_stream = streams_payload.get("heartrate") if isinstance(streams_payload, dict) else None
    time_stream = streams_payload.get("time") if isinstance(streams_payload, dict) else None

    heartrate_values = heartrate_stream.get("data") if isinstance(heartrate_stream, dict) else None
    if not isinstance(heartrate_values, list) or len(heartrate_values) == 0:
        return None

    time_values = time_stream.get("data") if isinstance(time_stream, dict) else None
    has_time_values = isinstance(time_values, list) and len(time_values) == len(heartrate_values)

    total_load = 0.0
    total_points = len(heartrate_values)

    for idx, hr_raw in enumerate(heartrate_values):
        try:
            hr_value = float(hr_raw)
        except (TypeError, ValueError):
            continue

        if hr_value <= 0:
            continue

        if has_time_values and idx < (total_points - 1):
            try:
                dt = int(time_values[idx + 1]) - int(time_values[idx])
            except (TypeError, ValueError):
                dt = 1
            sample_seconds = max(1, dt)
        else:
            sample_seconds = 1

        per_hour_load = softplus4_training_load_per_hour(hr_value, max_hr_bpm=max_hr_bpm)
        total_load += per_hour_load * (float(sample_seconds) / 3600.0)

    return float(round(total_load, 6))


def recompute_from_saved_streams(
    *,
    streams_dir: Path,
    overwrite: bool,
    activity_external_id_index: dict[int, str] | None,
) -> RecomputeStats:
    Base.metadata.create_all(bind=engine)
    run_sqlite_schema_updates()

    stats = RecomputeStats()
    db = SessionLocal()
    try:
        for stream_file in sorted(streams_dir.glob("*.json")):
            stats.stream_files_seen += 1

            try:
                activity_id = int(stream_file.stem)
            except ValueError:
                stats.streams_parse_error += 1
                continue

            session = _resolve_session_for_activity_id(db, activity_id)
            if session is None:
                session = _resolve_session_for_activity_id(
                    db,
                    activity_id,
                    activity_external_id_index=activity_external_id_index,
                )
            if session is None:
                stats.streams_missing_session += 1
                continue

            if (not overwrite) and (session.training_load is not None):
                continue

            try:
                streams_payload = json.loads(stream_file.read_text(encoding="utf-8"))
            except Exception:
                stats.streams_parse_error += 1
                continue

            training_load = _compute_training_load_from_stream_payload(
                streams_payload=streams_payload,
                max_hr_bpm=float(settings.TRAINING_LOAD_MAX_HR_BPM),
            )
            if training_load is None:
                stats.streams_missing_hr += 1
                continue

            session.training_load = float(training_load)
            stats.sessions_updated += 1

        db.commit()
        return stats
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recompute session.training_load from locally saved Strava stream JSON files."
    )
    parser.add_argument(
        "--streams-dir",
        type=Path,
        default=Path("backend/data/strava_streams_tmp"),
        help="Directory containing saved stream JSON files named <activity_id>.json",
    )
    parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Do not overwrite sessions that already have a training_load value.",
    )
    parser.add_argument(
        "--no-strava-index",
        action="store_true",
        help="Disable Strava activity index lookup for session resolution.",
    )
    args = parser.parse_args()

    streams_dir = args.streams_dir
    if not streams_dir.exists() or not streams_dir.is_dir():
        raise RuntimeError(f"Streams directory not found: {streams_dir}")

    activity_external_id_index: dict[int, str] | None = None
    if not args.no_strava_index:
        try:
            activity_external_id_index = _build_activity_external_id_index()
            print(f"Built Strava activity index entries={len(activity_external_id_index)}")
        except (StravaAPIError, Exception) as exc:
            print(f"WARN could not build Strava activity index: {exc}")

    stats = recompute_from_saved_streams(
        streams_dir=streams_dir,
        overwrite=(not args.no_overwrite),
        activity_external_id_index=activity_external_id_index,
    )

    print(
        "Recompute from saved streams complete: stream_files_seen={stream_files_seen} "
        "sessions_updated={sessions_updated} streams_missing_session={streams_missing_session} "
        "streams_missing_hr={streams_missing_hr} streams_parse_error={streams_parse_error}".format(
            stream_files_seen=stats.stream_files_seen,
            sessions_updated=stats.sessions_updated,
            streams_missing_session=stats.streams_missing_session,
            streams_missing_hr=stats.streams_missing_hr,
            streams_parse_error=stats.streams_parse_error,
        )
    )


if __name__ == "__main__":
    main()
