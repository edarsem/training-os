from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from app.core.database import Base, SessionLocal, engine
from app.core.config import settings
from app.core.strava import StravaAPIError, StravaClient
from app.models import models


@dataclass
class BackfillStats:
    pages_fetched: int = 0
    activities_seen: int = 0
    sessions_matched: int = 0
    updated: int = 0
    skipped_existing: int = 0
    skipped_no_session: int = 0
    skipped_no_activity_id: int = 0
    skipped_no_hr_stream: int = 0
    skipped_stream_error: int = 0
    streams_saved: int = 0
    streams_save_error: int = 0


def _activity_external_id(activity: dict) -> str | None:
    activity_id = activity.get("id")
    if activity_id is None:
        return None

    external_source_id = str(activity.get("external_id") or "").strip()
    if external_source_id:
        return external_source_id
    return f"strava:{int(activity_id)}"


def _session_has_training_load(session: models.Session | None) -> bool:
    if session is None:
        return False
    return session.training_load is not None


def _write_stream_file(*, streams_dir: Path, activity_id: int, streams: dict) -> None:
    streams_dir.mkdir(parents=True, exist_ok=True)
    out_file = streams_dir / f"{int(activity_id)}.json"
    out_file.write_text(json.dumps(streams, ensure_ascii=False), encoding="utf-8")


def backfill_hr_zones(
    *,
    max_sessions: int | None,
    per_page: int,
    max_pages: int,
    overwrite: bool,
    save_streams_dir: Path | None,
) -> BackfillStats:
    Base.metadata.create_all(bind=engine)

    client = StravaClient()
    stats = BackfillStats()

    db = SessionLocal()
    try:
        for page in range(1, max_pages + 1):
            page_data = client.get_activities_page(page=page, per_page=per_page)
            activities = page_data.get("activities", [])
            stats.pages_fetched += 1

            if not activities:
                break

            for activity in activities:
                stats.activities_seen += 1

                activity_id = activity.get("id")
                if activity_id is None:
                    stats.skipped_no_activity_id += 1
                    continue

                external_id = _activity_external_id(activity)
                if not external_id:
                    stats.skipped_no_session += 1
                    continue

                session = db.query(models.Session).filter(models.Session.external_id == external_id).first()
                if not session:
                    stats.skipped_no_session += 1
                    continue

                stats.sessions_matched += 1

                if (not overwrite) and _session_has_training_load(session):
                    stats.skipped_existing += 1
                    continue

                try:
                    threshold_hr = settings.TRAINING_LOAD_THRESHOLD_HR_BPM
                    metrics = client.get_activity_training_metrics(
                        activity_id=int(activity_id),
                        threshold_hr_bpm=(float(threshold_hr) if threshold_hr is not None else None),
                        include_streams=(save_streams_dir is not None),
                    )
                except StravaAPIError:
                    stats.skipped_stream_error += 1
                    continue
                if not metrics or metrics.get("training_load") is None:
                    stats.skipped_no_hr_stream += 1
                    continue

                if save_streams_dir is not None:
                    streams_payload = metrics.get("streams")
                    if isinstance(streams_payload, dict):
                        try:
                            _write_stream_file(
                                streams_dir=save_streams_dir,
                                activity_id=int(activity_id),
                                streams=streams_payload,
                            )
                            stats.streams_saved += 1
                        except Exception:
                            stats.streams_save_error += 1

                session.training_load = float(metrics.get("training_load"))
                db.commit()
                stats.updated += 1

                if max_sessions is not None and stats.updated >= max_sessions:
                    return stats

            if len(activities) < per_page:
                break

        return stats
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Strava HR-zone seconds into local DB.")
    parser.add_argument("--limit", type=int, default=5, help="Number of matched sessions to update (for quick test).")
    parser.add_argument("--all", action="store_true", help="Process full reachable Strava activity history.")
    parser.add_argument("--per-page", type=int, default=50, help="Strava activities fetched per page.")
    parser.add_argument("--max-pages", type=int, default=100, help="Safety cap for pages.")
    parser.add_argument("--overwrite", action="store_true", help="Recompute zone seconds even if already present.")
    parser.add_argument(
        "--save-streams-dir",
        type=Path,
        default=Path("backend/data/strava_streams_tmp"),
        help="Directory where raw stream payloads are saved temporarily (empty string disables saving).",
    )
    args = parser.parse_args()

    if args.all:
        max_sessions = None
    else:
        max_sessions = max(1, int(args.limit))

    save_streams_dir: Path | None = args.save_streams_dir
    if str(save_streams_dir).strip() == "":
        save_streams_dir = None

    try:
        stats = backfill_hr_zones(
            max_sessions=max_sessions,
            per_page=max(1, min(int(args.per_page), 200)),
            max_pages=max(1, int(args.max_pages)),
            overwrite=bool(args.overwrite),
            save_streams_dir=save_streams_dir,
        )
    except StravaAPIError as exc:
        print(f"ERROR Strava API ({exc.status_code}): {exc}")
        return
    except Exception as exc:
        print(f"ERROR: {exc}")
        return

    mode = "all history" if args.all else f"last {max_sessions} matched sessions"
    print(f"Done ({mode}).")
    print(
        "pages_fetched={pages_fetched} activities_seen={activities_seen} sessions_matched={sessions_matched} "
        "updated={updated} skipped_existing={skipped_existing} skipped_no_session={skipped_no_session} "
        "skipped_no_activity_id={skipped_no_activity_id} skipped_no_hr_stream={skipped_no_hr_stream} "
        "skipped_stream_error={skipped_stream_error} streams_saved={streams_saved} streams_save_error={streams_save_error}".format(
            pages_fetched=stats.pages_fetched,
            activities_seen=stats.activities_seen,
            sessions_matched=stats.sessions_matched,
            updated=stats.updated,
            skipped_existing=stats.skipped_existing,
            skipped_no_session=stats.skipped_no_session,
            skipped_no_activity_id=stats.skipped_no_activity_id,
            skipped_no_hr_stream=stats.skipped_no_hr_stream,
            skipped_stream_error=stats.skipped_stream_error,
            streams_saved=stats.streams_saved,
            streams_save_error=stats.streams_save_error,
        )
    )


if __name__ == "__main__":
    main()
