import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from app.core.database import get_db
from app.core.config import settings
from app.core.training_load_defaults import (
    DEFAULT_TRAINING_LOAD_ATL_DAYS,
    DEFAULT_TRAINING_LOAD_CTL_DAYS,
    TRAINING_LOAD_SOFTPLUS4_A,
    TRAINING_LOAD_SOFTPLUS4_B,
    TRAINING_LOAD_SOFTPLUS4_C,
    TRAINING_LOAD_SOFTPLUS4_D,
    softplus4_training_load_per_hour,
)
from app.core.strava import StravaAPIError, StravaClient, StravaConfigError
from app.llm.service import LLMConfigurationError, LLMProviderError, TrainingOSLLMService
from app.training_load import TrainingLoadConfig, compute_training_load_series
from app.training_load_recompute import recompute_training_load_from_date, recompute_training_load_full_history
from app.models import models
from app.schemas import schemas
from app.crud import crud

router = APIRouter()


def _map_strava_sport_type_to_session_type(sport_type: str | None) -> str:
    if not sport_type:
        return "other"
    normalized = sport_type.strip().lower()

    mapping = {
        "run": "run",
        "trailrun": "trail",
        "ride": "bike",
        "virtualride": "bike",
        "ebikeride": "bike",
        "gravelride": "bike",
        "mountainbikeride": "bike",
        "hike": "hike",
        "walk": "hike",
        "swim": "swim",
        "weightstraining": "strength",
        "weighttraining": "strength",
        "workout": "mobility",
        "indoorcardio": "mobility",
        "indoor_cardio": "mobility",
        "yoga": "mobility",
        "pilates": "mobility",
        "iceskate": "skate",
        "inlineskate": "skate",
    }
    return mapping.get(normalized, "other")


def _extract_strava_timezone_name(raw_timezone: str | None) -> str | None:
    if raw_timezone is None:
        return None
    text = str(raw_timezone).strip()
    if not text:
        return None

    if ")" in text:
        candidate = text.split(")", 1)[1].strip()
        if candidate:
            return candidate
    return text


def _parse_strava_start_date(start_date_raw: str | None) -> datetime | None:
    if not start_date_raw:
        return None
    normalized = start_date_raw.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _compute_activity_local_date(
    *,
    start_time_utc: datetime,
    timezone_name: str | None,
    start_date_local_raw: str | None,
) -> date:
    if start_date_local_raw:
        local_dt = _parse_strava_start_date(start_date_local_raw)
        if local_dt is not None:
            return local_dt.date()

    if timezone_name:
        try:
            tz = ZoneInfo(timezone_name)
            return start_time_utc.astimezone(tz).date()
        except Exception:
            pass

    return start_time_utc.date()


def _build_session_notes_from_strava(activity: dict) -> str | None:
    if activity.get("private"):
        return None
    name = str(activity.get("name") or "").strip()
    description = str(activity.get("description") or "").strip()
    if name and description:
        return f"{name}\n{description}"
    if name:
        return name
    if description:
        return description
    return None


def _map_strava_activity_to_session_payload(activity: dict) -> dict | None:
    strava_activity_id = activity.get("id")
    if strava_activity_id is None:
        return None

    start_time_utc = _parse_strava_start_date(activity.get("start_date"))
    if start_time_utc is None:
        return None

    timezone_name = _extract_strava_timezone_name(activity.get("timezone"))
    local_session_date = _compute_activity_local_date(
        start_time_utc=start_time_utc,
        timezone_name=timezone_name,
        start_date_local_raw=activity.get("start_date_local"),
    )

    moving_seconds = activity.get("moving_time_seconds")
    if moving_seconds is None:
        moving_seconds = activity.get("moving_time")

    elapsed_seconds = activity.get("elapsed_time_seconds")
    if elapsed_seconds is None:
        elapsed_seconds = activity.get("elapsed_time")

    if moving_seconds is None and elapsed_seconds is not None:
        moving_seconds = elapsed_seconds
    if elapsed_seconds is None and moving_seconds is not None:
        elapsed_seconds = moving_seconds

    moving_seconds = moving_seconds or 0
    elapsed_seconds = elapsed_seconds or moving_seconds

    moving_duration_minutes = max(1, int(round(float(moving_seconds) / 60)))
    elapsed_duration_minutes = max(1, int(round(float(elapsed_seconds) / 60)))
    duration_minutes = elapsed_duration_minutes

    distance_km = activity.get("distance_km")
    if distance_km is None and activity.get("distance") is not None:
        distance_km = round(float(activity.get("distance")) / 1000, 3)

    elevation_gain = activity.get("elevation_gain_m")
    if elevation_gain is None:
        elevation_gain = activity.get("total_elevation_gain")

    sport_type = activity.get("sport_type") or activity.get("type")
    mapped_type = _map_strava_sport_type_to_session_type(sport_type)

    notes = _build_session_notes_from_strava(activity)

    external_source_id = str(activity.get("external_id") or "").strip()
    if external_source_id:
        external_id = external_source_id
    else:
        external_id = f"strava:{strava_activity_id}"

    average_speed_m_per_s = activity.get("average_speed")
    average_pace_min_per_km = None
    if average_speed_m_per_s is not None:
        speed = float(average_speed_m_per_s)
        if speed > 0:
            average_pace_min_per_km = round((1000.0 / speed) / 60.0, 3)

    average_heart_rate = activity.get("average_heartrate")
    max_heart_rate = activity.get("max_heartrate")

    return {
        "strava_activity_id": int(strava_activity_id),
        "external_id": external_id,
        "date": local_session_date,
        "start_time": start_time_utc,
        "timezone_name": timezone_name,
        "mapped_type": mapped_type,
        "duration_minutes": duration_minutes,
        "elapsed_duration_minutes": elapsed_duration_minutes,
        "moving_duration_minutes": moving_duration_minutes,
        "distance_km": float(distance_km) if distance_km is not None else None,
        "elevation_gain_m": int(round(float(elevation_gain))) if elevation_gain is not None else None,
        "average_pace_min_per_km": average_pace_min_per_km,
        "average_heart_rate_bpm": float(average_heart_rate) if average_heart_rate is not None else None,
        "max_heart_rate_bpm": float(max_heart_rate) if max_heart_rate is not None else None,
        "training_load": (float(activity.get("training_load")) if activity.get("training_load") is not None else None),
        "hr_stream_json": activity.get("hr_stream_json"),
        "notes": notes,
        "name": activity.get("name"),
    }


def _enrich_activity_for_import(activity: dict, client: StravaClient) -> dict:
    activity_id = activity.get("id")
    if activity_id is None:
        return activity

    merged = dict(activity)

    try:
        detail = client.get_activity_by_id(int(activity_id)).get("activity", {})
        if isinstance(detail, dict):
            merged.update(detail)
    except Exception:
        pass

    try:
        threshold_hr = settings.TRAINING_LOAD_THRESHOLD_HR_BPM
        metrics = client.get_activity_training_metrics(
            activity_id=int(activity_id),
            threshold_hr_bpm=(float(threshold_hr) if threshold_hr is not None else None),
            include_streams=bool(settings.TRAINING_LOAD_STORE_HR_STREAMS_DEV),
        )
        if metrics:
            training_load = metrics.get("training_load")
            if training_load is not None:
                merged["training_load"] = float(training_load)
            zone_seconds = metrics.get("zone_seconds")
            if isinstance(zone_seconds, dict):
                merged["hr_zone_seconds"] = zone_seconds
            if bool(settings.TRAINING_LOAD_STORE_HR_STREAMS_DEV):
                streams = metrics.get("streams")
                if isinstance(streams, dict):
                    merged["hr_stream_json"] = json.dumps(streams, ensure_ascii=False)
    except Exception:
        pass

    return merged


def _upsert_strava_activities(
    db: Session,
    client: StravaClient,
    activities: List[dict],
) -> tuple[int, int, int, List[schemas.StravaImportItemResponse]]:
    imported_count = 0
    updated_count = 0
    skipped_count = 0
    items: List[schemas.StravaImportItemResponse] = []

    for activity in activities:
        enriched_activity = _enrich_activity_for_import(activity, client)
        payload = _map_strava_activity_to_session_payload(enriched_activity)
        if payload is None:
            skipped_count += 1
            items.append(
                schemas.StravaImportItemResponse(
                    action="skipped",
                    name=enriched_activity.get("name"),
                )
            )
            continue

        existing = db.query(models.Session).filter(models.Session.external_id == payload["external_id"]).first()
        if existing:
            existing.date = payload["date"]
            existing.start_time = payload["start_time"]
            existing.timezone_name = payload["timezone_name"]
            existing.type = payload["mapped_type"]
            existing.duration_minutes = payload["duration_minutes"]
            existing.elapsed_duration_minutes = payload["elapsed_duration_minutes"]
            existing.moving_duration_minutes = payload["moving_duration_minutes"]
            existing.distance_km = payload["distance_km"]
            existing.elevation_gain_m = payload["elevation_gain_m"]
            existing.average_pace_min_per_km = payload["average_pace_min_per_km"]
            existing.average_heart_rate_bpm = payload["average_heart_rate_bpm"]
            existing.max_heart_rate_bpm = payload["max_heart_rate_bpm"]
            if payload["training_load"] is not None:
                existing.training_load = payload["training_load"]
            if payload.get("hr_stream_json") is not None:
                existing.hr_stream_json = payload.get("hr_stream_json")
            existing.notes = payload["notes"]
            updated_count += 1
            action = "updated"
            session_id = existing.id
        else:
            new_session = models.Session(
                date=payload["date"],
                start_time=payload["start_time"],
                external_id=payload["external_id"],
                timezone_name=payload["timezone_name"],
                type=payload["mapped_type"],
                duration_minutes=payload["duration_minutes"],
                elapsed_duration_minutes=payload["elapsed_duration_minutes"],
                moving_duration_minutes=payload["moving_duration_minutes"],
                distance_km=payload["distance_km"],
                elevation_gain_m=payload["elevation_gain_m"],
                average_pace_min_per_km=payload["average_pace_min_per_km"],
                average_heart_rate_bpm=payload["average_heart_rate_bpm"],
                max_heart_rate_bpm=payload["max_heart_rate_bpm"],
                training_load=payload["training_load"],
                hr_stream_json=payload.get("hr_stream_json"),
                notes=payload["notes"],
            )
            db.add(new_session)
            db.flush()
            imported_count += 1
            action = "imported"
            session_id = new_session.id

        zone_seconds = enriched_activity.get("hr_zone_seconds")
        if isinstance(zone_seconds, dict):
            crud.upsert_session_hr_zone_time(
                db,
                int(session_id),
                zone_seconds,
            )

        items.append(
            schemas.StravaImportItemResponse(
                strava_activity_id=payload["strava_activity_id"],
                external_id=payload["external_id"],
                session_id=session_id,
                action=action,
                mapped_type=payload["mapped_type"],
                session_date=payload["date"],
                name=payload["name"],
            )
        )

    return imported_count, updated_count, skipped_count, items


def _get_oldest_changed_session_date(items: List[schemas.StravaImportItemResponse]) -> date | None:
    changed_dates = [
        item.session_date
        for item in items
        if item.action in {"imported", "updated"} and item.session_date is not None
    ]
    if not changed_dates:
        return None
    return min(changed_dates)

# --- Sessions ---
@router.get("/sessions", response_model=List[schemas.SessionResponse])
def read_sessions(start_date: date, end_date: date, db: Session = Depends(get_db)):
    """Get all sessions within a date range."""
    return crud.get_sessions_by_date_range(db, start_date, end_date)

@router.post("/sessions", response_model=schemas.SessionResponse)
def create_session(session: schemas.SessionCreate, db: Session = Depends(get_db)):
    """Create a new training session."""
    return crud.create_session(db, session)

@router.put("/sessions/{session_id}", response_model=schemas.SessionResponse)
def update_session(session_id: int, session: schemas.SessionUpdate, db: Session = Depends(get_db)):
    """Update an existing session."""
    db_session = crud.update_session(db, session_id, session)
    if not db_session:
        raise HTTPException(status_code=404, detail="Session not found")
    return db_session

@router.delete("/sessions/{session_id}")
def delete_session(session_id: int, db: Session = Depends(get_db)):
    """Delete a session."""
    success = crud.delete_session(db, session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


@router.get("/sessions/{session_id}/hr-zones", response_model=schemas.SessionHRZonesResponse)
def read_session_hr_zones(session_id: int, db: Session = Depends(get_db)):
    session = crud.get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    zone_map = crud.get_session_hr_zone_time_map(db, [session_id])
    zone_seconds = zone_map.get(session_id, {
        "zone_0_seconds": 0,
        "zone_1_seconds": 0,
        "zone_2_seconds": 0,
        "zone_3_seconds": 0,
        "zone_4_seconds": 0,
        "zone_5_seconds": 0,
        "zone_6_seconds": 0,
    })
    seconds_only = {
        key: int(value or 0)
        for key, value in zone_seconds.items()
        if key.endswith("_seconds")
    }
    total_seconds = int(sum(seconds_only.values()))

    return schemas.SessionHRZonesResponse(
        session_id=session_id,
        zone_seconds=seconds_only,
        total_seconds=total_seconds,
    )

# --- Day Notes ---
@router.get("/day-notes", response_model=List[schemas.DayNoteResponse])
def read_day_notes(start_date: date, end_date: date, db: Session = Depends(get_db)):
    """Get day notes within a date range."""
    return crud.get_day_notes_by_date_range(db, start_date, end_date)

@router.post("/day-notes", response_model=schemas.DayNoteResponse)
def upsert_day_note(note: schemas.DayNoteCreate, db: Session = Depends(get_db)):
    """Create or update a day note."""
    return crud.upsert_day_note(db, note)

# --- Weekly Plans ---
@router.get("/plans/{year}/{week_number}", response_model=schemas.WeeklyPlanResponse)
def read_weekly_plan(year: int, week_number: int, db: Session = Depends(get_db)):
    """Get the training plan for a specific week."""
    plan = crud.get_weekly_plan(db, year, week_number)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan

@router.post("/plans", response_model=schemas.WeeklyPlanResponse)
def upsert_weekly_plan(plan: schemas.WeeklyPlanCreate, db: Session = Depends(get_db)):
    """Create or update a weekly plan."""
    return crud.upsert_weekly_plan(db, plan)


# --- Chat History ---
@router.get("/chat/conversations", response_model=List[schemas.ChatConversationSummaryResponse])
def list_chat_conversations(db: Session = Depends(get_db), limit: int = Query(default=100, ge=1, le=500)):
    conversations = crud.list_chat_conversations(db, limit=limit)
    out: List[schemas.ChatConversationSummaryResponse] = []
    for conversation in conversations:
        messages = crud.list_chat_messages(db, conversation.id)
        if len(messages) == 0:
            continue
        last_content = messages[-1].content if messages else None
        out.append(
            schemas.ChatConversationSummaryResponse(
                id=conversation.id,
                title=conversation.title,
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
                message_count=len(messages),
                last_message_preview=(last_content[:140] if last_content else None),
            )
        )
    return out


@router.post("/chat/conversations", response_model=schemas.ChatConversationResponse)
def create_chat_conversation(payload: schemas.ChatConversationCreate, db: Session = Depends(get_db)):
    return crud.create_chat_conversation(db, title=payload.title)


@router.delete("/chat/conversations/{conversation_id}")
def delete_chat_conversation(conversation_id: int, db: Session = Depends(get_db)):
    success = crud.delete_chat_conversation(db, conversation_id)
    if not success:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@router.get("/chat/conversations/{conversation_id}/messages", response_model=List[schemas.ChatMessageResponse])
def list_chat_messages(conversation_id: int, db: Session = Depends(get_db)):
    conversation = crud.get_chat_conversation(db, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return crud.list_chat_messages(db, conversation_id)


@router.post("/chat/conversations/{conversation_id}/messages", response_model=schemas.ChatMessageResponse)
def create_chat_message(conversation_id: int, payload: schemas.ChatMessageCreate, db: Session = Depends(get_db)):
    conversation = crud.get_chat_conversation(db, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    role = (payload.role or "").strip().lower()
    if role not in {"user", "assistant"}:
        raise HTTPException(status_code=400, detail="Invalid role. Use 'user' or 'assistant'.")

    return crud.create_chat_message(db, conversation_id, role=role, content=payload.content)

# --- Intelligence / Summaries ---
@router.get("/summary/week/{year}/{week_number}", response_model=schemas.WeekSummaryResponse)
def get_week_summary(year: int, week_number: int, db: Session = Depends(get_db)):
    """Get a comprehensive summary of a week (plan + actuals)."""
    # Calculate start and end dates of the ISO week
    start_date = date.fromisocalendar(year, week_number, 1)
    end_date = start_date + timedelta(days=6)
    
    plan = crud.get_weekly_plan(db, year, week_number)
    sessions = crud.get_sessions_by_date_range(db, start_date, end_date)
    day_notes = crud.get_day_notes_by_date_range(db, start_date, end_date)
    
    total_duration = sum(s.duration_minutes for s in sessions)
    
    # Distance counts for run and trail
    total_distance = sum(s.distance_km or 0 for s in sessions if s.type in ['run', 'trail'])
    
    # Elevation counts for run, trail, and hike
    total_elevation = sum(s.elevation_gain_m or 0 for s in sessions if s.type in ['run', 'trail', 'hike'])
    
    return schemas.WeekSummaryResponse(
        year=year,
        week_number=week_number,
        plan=plan,
        sessions=sessions,
        day_notes=day_notes,
        total_duration_minutes=total_duration,
        total_distance_km=total_distance,
        total_elevation_gain_m=total_elevation
    )


@router.get("/training-load", response_model=schemas.TrainingLoadResponse)
def get_training_load(
    start_date: date | None = None,
    end_date: date | None = None,
    db: Session = Depends(get_db),
):
    today = date.today()
    first_session_date = crud.get_first_session_date(db)

    resolved_end = end_date or today
    resolved_start = start_date or first_session_date or resolved_end

    if resolved_end < resolved_start:
        raise HTTPException(status_code=400, detail="end_date must be greater than or equal to start_date")

    persisted_daily = crud.get_daily_training_load_by_date_range(db, resolved_start, resolved_end)
    if persisted_daily:
        current_atl = float(persisted_daily[-1].atl)
        current_ctl = float(persisted_daily[-1].ctl)
        current_acwr = float(persisted_daily[-1].acwr) if persisted_daily[-1].acwr is not None else None

        assumptions = [
            "Daily ATL/CTL/ACWR values are loaded from persisted history.",
            "Session loads are precomputed and stored for fast curve retrieval.",
            "Session load uses softplus4 over HR stream and ATL/CTL constants use project-level defaults.",
        ]

        return schemas.TrainingLoadResponse(
            start_date=resolved_start,
            end_date=resolved_end,
            current_atl=current_atl,
            current_ctl=current_ctl,
            current_acwr=current_acwr,
            config=schemas.TrainingLoadConfigResponse(
                function="softplus4",
                softplus4_a=float(TRAINING_LOAD_SOFTPLUS4_A),
                softplus4_b=float(TRAINING_LOAD_SOFTPLUS4_B),
                softplus4_c=float(TRAINING_LOAD_SOFTPLUS4_C),
                softplus4_d=float(TRAINING_LOAD_SOFTPLUS4_D),
                atl_time_constant_days=float(DEFAULT_TRAINING_LOAD_ATL_DAYS),
                ctl_time_constant_days=float(DEFAULT_TRAINING_LOAD_CTL_DAYS),
            ),
            assumptions=assumptions,
            daily=[
                schemas.TrainingLoadDailyPoint(
                    date=point.date,
                    load=float(point.load),
                    atl=float(point.atl),
                    ctl=float(point.ctl),
                    acwr=float(point.acwr) if point.acwr is not None else None,
                    zone_minutes={},
                    missing_hr_minutes=0,
                    session_breakdown=[],
                )
                for point in persisted_daily
            ],
        )

    warmup_start = first_session_date or resolved_start
    sessions = crud.get_sessions_by_date_range(db, warmup_start, resolved_end)

    config = TrainingLoadConfig(
        atl_time_constant_days=float(DEFAULT_TRAINING_LOAD_ATL_DAYS),
        ctl_time_constant_days=float(DEFAULT_TRAINING_LOAD_CTL_DAYS),
    )

    computed = compute_training_load_series(
        sessions=sessions,
        session_zone_time_map={},
        start_date=warmup_start,
        end_date=resolved_end,
        config=config,
    )

    filtered_daily = [point for point in computed["daily"] if point["date"] >= resolved_start]

    current_atl = float(filtered_daily[-1]["atl"]) if filtered_daily else 0.0
    current_ctl = float(filtered_daily[-1]["ctl"]) if filtered_daily else 0.0
    current_acwr = filtered_daily[-1]["acwr"] if filtered_daily else None

    assumptions = [
        "Per-session load is integrated directly from Strava HR streams with softplus4 mapping at import/backfill time.",
        "Sessions missing persisted stream-derived load contribute 0 load until backfilled.",
        "Softplus4 parameters and ATL/CTL constants currently use project-level defaults.",
    ]

    return schemas.TrainingLoadResponse(
        start_date=resolved_start,
        end_date=resolved_end,
        current_atl=current_atl,
        current_ctl=current_ctl,
        current_acwr=current_acwr,
        config=schemas.TrainingLoadConfigResponse(**computed["config"]),
        assumptions=assumptions,
        daily=[schemas.TrainingLoadDailyPoint(**point) for point in filtered_daily],
    )


@router.get("/training-load/softplus4-curve", response_model=schemas.Softplus4CurveResponse)
def get_softplus4_curve(
    max_hr_bpm: float = Query(default=200.0, ge=1.0, le=260.0),
    hr_start_bpm: float = Query(default=40.0, ge=1.0, le=260.0),
    hr_end_bpm: float = Query(default=220.0, ge=1.0, le=300.0),
    hr_step_bpm: float = Query(default=1.0, gt=0.0, le=20.0),
):
    if hr_end_bpm < hr_start_bpm:
        raise HTTPException(status_code=400, detail="hr_end_bpm must be greater than or equal to hr_start_bpm")

    points: List[schemas.Softplus4CurvePoint] = []
    min_value: float | None = None
    min_value_hr: float | None = None
    negative_points = 0

    hr = float(hr_start_bpm)
    max_iterations = 10000
    iterations = 0

    while hr <= float(hr_end_bpm) + 1e-9:
        value = float(softplus4_training_load_per_hour(hr, max_hr_bpm=float(max_hr_bpm)))
        hr_rounded = round(float(hr), 3)
        value_rounded = round(value, 6)

        points.append(
            schemas.Softplus4CurvePoint(
                hr_bpm=hr_rounded,
                training_load_per_hour=value_rounded,
            )
        )

        if min_value is None or value < min_value:
            min_value = value
            min_value_hr = hr_rounded

        if value < 0:
            negative_points += 1

        hr += float(hr_step_bpm)
        iterations += 1
        if iterations > max_iterations:
            raise HTTPException(status_code=400, detail="Too many points requested")

    return schemas.Softplus4CurveResponse(
        softplus4_a=float(TRAINING_LOAD_SOFTPLUS4_A),
        softplus4_b=float(TRAINING_LOAD_SOFTPLUS4_B),
        softplus4_c=float(TRAINING_LOAD_SOFTPLUS4_C),
        softplus4_d=float(TRAINING_LOAD_SOFTPLUS4_D),
        max_hr_bpm=float(max_hr_bpm),
        hr_start_bpm=float(hr_start_bpm),
        hr_end_bpm=float(hr_end_bpm),
        hr_step_bpm=float(hr_step_bpm),
        min_value=round(float(min_value or 0.0), 6),
        min_value_hr_bpm=float(min_value_hr or hr_start_bpm),
        negative_points=int(negative_points),
        points=points,
    )


@router.get(
    "/integrations/strava/activities/recent",
    response_model=schemas.StravaRecentActivitiesResponse,
)
def get_recent_strava_activities(
    limit: int = Query(default=2, ge=1, le=30)
):
    client = StravaClient()
    try:
        result = client.get_recent_activities(limit=limit)
        return schemas.StravaRecentActivitiesResponse(**result)
    except StravaConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except StravaAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post(
    "/integrations/strava/import/recent",
    response_model=schemas.StravaImportResponse,
)
def import_recent_strava_activities(
    limit: int = Query(default=2, ge=1, le=30),
    db: Session = Depends(get_db),
):
    client = StravaClient()
    try:
        page_data = client.get_activities_page(page=1, per_page=limit)
    except StravaConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except StravaAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    imported_count, updated_count, skipped_count, items = _upsert_strava_activities(
        db=db,
        client=client,
        activities=page_data.get("activities", []),
    )

    recompute_result = None
    oldest_changed_date = _get_oldest_changed_session_date(items)
    if oldest_changed_date is not None:
        recompute_result = recompute_training_load_from_date(db, oldest_changed_date)

    db.commit()

    return schemas.StravaImportResponse(
        fetched_count=int(page_data.get("fetched_count", 0)),
        checked_count=int(page_data.get("fetched_count", 0)),
        pages_fetched=1,
        stopped_on_existing=False,
        imported_count=imported_count,
        updated_count=updated_count,
        skipped_count=skipped_count,
        auto_refreshed_token=bool(page_data.get("auto_refreshed_token", False)),
        training_load_recomputed_from=(recompute_result.recomputed_from_date if recompute_result else None),
        training_load_recomputed_to=(recompute_result.recomputed_to_date if recompute_result else None),
        training_load_days_recomputed=(recompute_result.days_recomputed if recompute_result else 0),
        training_load_sessions_updated=(recompute_result.sessions_updated if recompute_result else 0),
        items=items,
    )


@router.post(
    "/integrations/strava/import/refresh",
    response_model=schemas.StravaImportResponse,
)
def refresh_strava_activities_until_known(
    per_page: int = Query(default=30, ge=5, le=100),
    max_pages: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    client = StravaClient()

    all_new_activities: List[dict] = []
    all_existing_missing_tl_activities: List[dict] = []
    checked_count = 0
    pages_fetched = 0
    stopped_on_existing = False
    auto_refreshed_any = False

    try:
        for page in range(1, max_pages + 1):
            page_data = client.get_activities_page(page=page, per_page=per_page)
            page_activities = page_data.get("activities", [])
            pages_fetched += 1
            auto_refreshed_any = auto_refreshed_any or bool(page_data.get("auto_refreshed_token", False))

            if not page_activities:
                break

            for activity in page_activities:
                checked_count += 1
                payload = _map_strava_activity_to_session_payload(activity)
                if payload is None:
                    continue
                external_id = payload["external_id"]
                exists = db.query(models.Session).filter(models.Session.external_id == external_id).first()
                if exists:
                    if exists.training_load is None or float(exists.training_load) <= 0.0:
                        all_existing_missing_tl_activities.append(activity)
                        continue
                    stopped_on_existing = True
                    break
                all_new_activities.append(activity)

            if stopped_on_existing or len(page_activities) < per_page:
                break
    except StravaConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except StravaAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    activities_to_upsert = all_new_activities + all_existing_missing_tl_activities

    imported_count, updated_count, skipped_count, items = _upsert_strava_activities(
        db=db,
        client=client,
        activities=activities_to_upsert,
    )

    recompute_result = None
    oldest_changed_date = _get_oldest_changed_session_date(items)
    if oldest_changed_date is not None:
        recompute_result = recompute_training_load_from_date(db, oldest_changed_date)

    db.commit()

    return schemas.StravaImportResponse(
        fetched_count=len(activities_to_upsert),
        checked_count=checked_count,
        pages_fetched=pages_fetched,
        stopped_on_existing=stopped_on_existing,
        imported_count=imported_count,
        updated_count=updated_count,
        skipped_count=skipped_count,
        auto_refreshed_token=auto_refreshed_any,
        training_load_recomputed_from=(recompute_result.recomputed_from_date if recompute_result else None),
        training_load_recomputed_to=(recompute_result.recomputed_to_date if recompute_result else None),
        training_load_days_recomputed=(recompute_result.days_recomputed if recompute_result else 0),
        training_load_sessions_updated=(recompute_result.sessions_updated if recompute_result else 0),
        items=items,
    )


@router.post(
    "/integrations/strava/import/backfill",
    response_model=schemas.StravaImportResponse,
)
def backfill_strava_activities(
    per_page: int = Query(default=100, ge=5, le=100),
    max_pages: int = Query(default=40, ge=1, le=200),
    db: Session = Depends(get_db),
):
    client = StravaClient()

    all_activities: List[dict] = []
    checked_count = 0
    pages_fetched = 0
    auto_refreshed_any = False

    try:
        for page in range(1, max_pages + 1):
            page_data = client.get_activities_page(page=page, per_page=per_page)
            page_activities = page_data.get("activities", [])
            pages_fetched += 1
            auto_refreshed_any = auto_refreshed_any or bool(page_data.get("auto_refreshed_token", False))

            if not page_activities:
                break

            checked_count += len(page_activities)
            all_activities.extend(page_activities)

            if len(page_activities) < per_page:
                break
    except StravaConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except StravaAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    imported_count, updated_count, skipped_count, items = _upsert_strava_activities(
        db=db,
        client=client,
        activities=all_activities,
    )

    recompute_result = None
    oldest_changed_date = _get_oldest_changed_session_date(items)
    if oldest_changed_date is not None:
        recompute_result = recompute_training_load_from_date(db, oldest_changed_date)

    db.commit()

    return schemas.StravaImportResponse(
        fetched_count=len(all_activities),
        checked_count=checked_count,
        pages_fetched=pages_fetched,
        stopped_on_existing=False,
        imported_count=imported_count,
        updated_count=updated_count,
        skipped_count=skipped_count,
        auto_refreshed_token=auto_refreshed_any,
        training_load_recomputed_from=(recompute_result.recomputed_from_date if recompute_result else None),
        training_load_recomputed_to=(recompute_result.recomputed_to_date if recompute_result else None),
        training_load_days_recomputed=(recompute_result.days_recomputed if recompute_result else 0),
        training_load_sessions_updated=(recompute_result.sessions_updated if recompute_result else 0),
        items=items,
    )


@router.post("/training-load/recompute-all", response_model=schemas.TrainingLoadRecomputeResponse)
def recompute_all_training_load(db: Session = Depends(get_db)):
    try:
        result = recompute_training_load_full_history(db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return schemas.TrainingLoadRecomputeResponse(
        recomputed_from_date=result.recomputed_from_date,
        recomputed_to_date=result.recomputed_to_date,
        days_recomputed=result.days_recomputed,
        sessions_updated=result.sessions_updated,
        current_atl=result.current_atl,
        current_ctl=result.current_ctl,
        current_acwr=result.current_acwr,
    )


@router.post(
    "/llm/interpret",
    response_model=schemas.LLMInterpretResponse,
)
def interpret_training_data_with_llm(
    payload: schemas.LLMInterpretRequest,
    db: Session = Depends(get_db),
):
    service = TrainingOSLLMService(db)
    try:
        return service.interpret(payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
