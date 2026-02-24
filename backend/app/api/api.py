from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List
from datetime import date, datetime, timedelta
from app.core.database import get_db
from app.core.strava import StravaAPIError, StravaClient, StravaConfigError
from app.llm.service import LLMConfigurationError, LLMProviderError, TrainingOSLLMService
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
        "workout": "strength",
        "yoga": "mobility",
        "pilates": "mobility",
        "iceskate": "skate",
        "inlineskate": "skate",
    }
    return mapping.get(normalized, "other")


def _parse_strava_start_date(start_date_raw: str | None) -> datetime | None:
    if not start_date_raw:
        return None
    normalized = start_date_raw.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


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

    start_time = _parse_strava_start_date(activity.get("start_date"))
    if start_time is None:
        return None

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
        "date": start_time.date(),
        "start_time": start_time,
        "mapped_type": mapped_type,
        "duration_minutes": duration_minutes,
        "elapsed_duration_minutes": elapsed_duration_minutes,
        "moving_duration_minutes": moving_duration_minutes,
        "distance_km": float(distance_km) if distance_km is not None else None,
        "elevation_gain_m": int(round(float(elevation_gain))) if elevation_gain is not None else None,
        "average_pace_min_per_km": average_pace_min_per_km,
        "average_heart_rate_bpm": float(average_heart_rate) if average_heart_rate is not None else None,
        "max_heart_rate_bpm": float(max_heart_rate) if max_heart_rate is not None else None,
        "notes": notes,
        "name": activity.get("name"),
    }


def _enrich_activity_for_import(activity: dict, client: StravaClient) -> dict:
    activity_id = activity.get("id")
    if activity_id is None:
        return activity

    if activity.get("private"):
        return activity

    has_description = str(activity.get("description") or "").strip() != ""
    if has_description:
        return activity

    try:
        detail = client.get_activity_by_id(int(activity_id)).get("activity", {})
    except Exception:
        return activity

    if not isinstance(detail, dict):
        return activity

    merged = dict(activity)
    merged.update(detail)
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
            existing.type = payload["mapped_type"]
            existing.duration_minutes = payload["duration_minutes"]
            existing.elapsed_duration_minutes = payload["elapsed_duration_minutes"]
            existing.moving_duration_minutes = payload["moving_duration_minutes"]
            existing.distance_km = payload["distance_km"]
            existing.elevation_gain_m = payload["elevation_gain_m"]
            existing.average_pace_min_per_km = payload["average_pace_min_per_km"]
            existing.average_heart_rate_bpm = payload["average_heart_rate_bpm"]
            existing.max_heart_rate_bpm = payload["max_heart_rate_bpm"]
            existing.notes = payload["notes"]
            updated_count += 1
            action = "updated"
            session_id = existing.id
        else:
            new_session = models.Session(
                date=payload["date"],
                start_time=payload["start_time"],
                external_id=payload["external_id"],
                type=payload["mapped_type"],
                duration_minutes=payload["duration_minutes"],
                elapsed_duration_minutes=payload["elapsed_duration_minutes"],
                moving_duration_minutes=payload["moving_duration_minutes"],
                distance_km=payload["distance_km"],
                elevation_gain_m=payload["elevation_gain_m"],
                average_pace_min_per_km=payload["average_pace_min_per_km"],
                average_heart_rate_bpm=payload["average_heart_rate_bpm"],
                max_heart_rate_bpm=payload["max_heart_rate_bpm"],
                notes=payload["notes"],
            )
            db.add(new_session)
            db.flush()
            imported_count += 1
            action = "imported"
            session_id = new_session.id

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
                exists = db.query(models.Session.id).filter(models.Session.external_id == external_id).first()
                if exists:
                    stopped_on_existing = True
                    break
                all_new_activities.append(activity)

            if stopped_on_existing or len(page_activities) < per_page:
                break
    except StravaConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except StravaAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    imported_count, updated_count, skipped_count, items = _upsert_strava_activities(
        db=db,
        client=client,
        activities=all_new_activities,
    )
    db.commit()

    return schemas.StravaImportResponse(
        fetched_count=len(all_new_activities),
        checked_count=checked_count,
        pages_fetched=pages_fetched,
        stopped_on_existing=stopped_on_existing,
        imported_count=imported_count,
        updated_count=updated_count,
        skipped_count=skipped_count,
        auto_refreshed_token=auto_refreshed_any,
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
        items=items,
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
