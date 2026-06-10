import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session
from typing import List
from datetime import date, datetime, timedelta, timezone
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
from app.core.gpx import (
    GPXProcessingError,
    compare_route_with_activity,
    compute_slope_histogram,
    interpolate_point_at_distance,
    process_gpx,
    process_streams,
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
    normalized_simple = (
        normalized.replace("_", " ")
        .replace("-", " ")
        .replace(".", " ")
    )
    normalized_simple = " ".join(normalized_simple.split())

    if "cardio" in normalized_simple:
        if any(token in normalized_simple for token in {"int", "indoor", "interieur"}):
            return "mobility"
        if any(token in normalized_simple for token in {"ext", "outdoor", "exterieur"}):
            return "other"

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
        "cardio int": "mobility",
        "cardio intérieur": "mobility",
        "cardio interieur": "mobility",
        "cardio indoor": "mobility",
        "cardio ext": "other",
        "cardio extérieur": "other",
        "cardio exterieur": "other",
        "cardio outdoor": "other",
        "cartio ext": "other",
        "iceskate": "skate",
        "inlineskate": "skate",
    }
    return mapping.get(normalized_simple, mapping.get(normalized, "other"))


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
        "is_race": False,
        "training_load": (float(activity.get("training_load")) if activity.get("training_load") is not None else None),
        "training_load_elapsed": (
            float(activity.get("training_load_elapsed"))
            if activity.get("training_load_elapsed") is not None
            else None
        ),
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
            training_load_elapsed = metrics.get("training_load_elapsed")
            if training_load_elapsed is not None:
                merged["training_load_elapsed"] = float(training_load_elapsed)
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
            if payload["training_load_elapsed"] is not None:
                existing.training_load_elapsed = payload["training_load_elapsed"]
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
                type=payload["mapped_type"],
                duration_minutes=payload["duration_minutes"],
                elapsed_duration_minutes=payload["elapsed_duration_minutes"],
                moving_duration_minutes=payload["moving_duration_minutes"],
                distance_km=payload["distance_km"],
                elevation_gain_m=payload["elevation_gain_m"],
                average_pace_min_per_km=payload["average_pace_min_per_km"],
                average_heart_rate_bpm=payload["average_heart_rate_bpm"],
                max_heart_rate_bpm=payload["max_heart_rate_bpm"],
                is_race=bool(payload.get("is_race", False)),
                training_load=payload["training_load"],
                training_load_elapsed=payload["training_load_elapsed"],
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

@router.get("/sessions/races", response_model=List[schemas.SessionResponse])
def read_race_sessions(db: Session = Depends(get_db)):
    """Get all sessions marked as race, oldest first."""
    return crud.get_race_sessions(db)


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

# --- Routes ---
def _route_detail_response(route: models.Route, markers: list[models.RouteMarker]) -> schemas.RouteDetailResponse:
    track = json.loads(route.track_json)
    histogram = compute_slope_histogram(track.get("slope_pct"), float(track.get("interval_m", 20.0)))
    return schemas.RouteDetailResponse(
        id=route.id,
        name=route.name,
        notes=route.notes,
        source_filename=route.source_filename,
        distance_km=route.distance_km,
        elevation_gain_m=route.elevation_gain_m,
        elevation_loss_m=route.elevation_loss_m,
        min_elevation_m=route.min_elevation_m,
        max_elevation_m=route.max_elevation_m,
        has_elevation=bool(route.has_elevation),
        session_id=route.session_id,
        created_at=route.created_at,
        updated_at=route.updated_at,
        track=track,
        markers=[schemas.RouteMarkerResponse.model_validate(m) for m in markers],
        slope_histogram=histogram,
    )


@router.post("/routes/upload", response_model=schemas.RouteDetailResponse)
async def upload_route(
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    """Upload a .gpx file and create a route with processed track data."""
    filename = file.filename or "route.gpx"
    if not filename.lower().endswith(".gpx"):
        raise HTTPException(status_code=400, detail="File must be a .gpx file")

    raw = await file.read()
    try:
        xml_text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="GPX file is not valid UTF-8 text")

    try:
        processed = process_gpx(xml_text)
    except GPXProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    route_name = (name or "").strip() or processed["name"] or filename.rsplit(".", 1)[0]

    route = crud.create_route(
        db,
        name=route_name,
        source_filename=filename,
        gpx_xml=xml_text,
        track_json=json.dumps(processed["track"], ensure_ascii=False),
        distance_km=processed["distance_km"],
        elevation_gain_m=processed["elevation_gain_m"],
        elevation_loss_m=processed["elevation_loss_m"],
        min_elevation_m=processed["min_elevation_m"],
        max_elevation_m=processed["max_elevation_m"],
        has_elevation=processed["has_elevation"],
    )
    return _route_detail_response(route, [])


@router.get("/routes", response_model=List[schemas.RouteSummaryResponse])
def list_routes(db: Session = Depends(get_db)):
    """List all routes (summaries only, no track data)."""
    return crud.list_routes(db)


@router.get("/routes/{route_id}", response_model=schemas.RouteDetailResponse)
def get_route(route_id: int, db: Session = Depends(get_db)):
    """Get a route with full track arrays, markers and slope histogram."""
    route = crud.get_route(db, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    return _route_detail_response(route, crud.list_route_markers(db, route_id))


@router.put("/routes/{route_id}", response_model=schemas.RouteSummaryResponse)
def update_route(route_id: int, payload: schemas.RouteUpdate, db: Session = Depends(get_db)):
    """Update route name / notes / linked session."""
    route = crud.update_route(db, route_id, payload)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    return route


@router.delete("/routes/{route_id}")
def delete_route(route_id: int, db: Session = Depends(get_db)):
    """Delete a route (markers cascade)."""
    if not crud.delete_route(db, route_id):
        raise HTTPException(status_code=404, detail="Route not found")
    return {"ok": True}


def _find_strava_activity_for_session(client: StravaClient, session: models.Session) -> dict | None:
    """Find the Strava activity matching a session by external_id, falling back to nearest start time."""
    if session.external_id and session.external_id.startswith("strava:"):
        try:
            activity_id = int(session.external_id.split(":", 1)[1])
            return client.get_activity_by_id(activity_id).get("activity")
        except (ValueError, StravaAPIError):
            pass

    base_dt = session.start_time or datetime.combine(session.date, datetime.min.time())
    after_epoch = int(base_dt.timestamp()) - 2 * 86400
    before_epoch = int(base_dt.timestamp()) + 2 * 86400
    activities = client.find_activities_in_window(after_epoch=after_epoch, before_epoch=before_epoch, per_page=30)

    external_id = (session.external_id or "").strip()
    if external_id:
        for activity in activities:
            if str(activity.get("external_id") or "").strip() == external_id:
                return activity

    if session.start_time is not None:
        session_start = session.start_time
        if session_start.tzinfo is None:
            session_start = session_start.replace(tzinfo=timezone.utc)
        best = None
        best_delta = None
        for activity in activities:
            start = _parse_strava_start_date(activity.get("start_date"))
            if start is None:
                continue
            delta = abs((start - session_start).total_seconds())
            if best_delta is None or delta < best_delta:
                best, best_delta = activity, delta
        if best is not None and best_delta is not None and best_delta < 3600:
            return best
    return None


def _build_comparison_response(route: models.Route, session: models.Session) -> schemas.RouteComparisonResponse:
    streams = json.loads(session.gps_stream_json)
    track = json.loads(route.track_json)
    try:
        comparison = compare_route_with_activity(track, streams)
    except GPXProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    name = None
    if session.notes:
        name = str(session.notes).splitlines()[0].strip() or None

    return schemas.RouteComparisonResponse(
        route_id=route.id,
        session_id=session.id,
        session_date=session.date,
        session_type=session.type,
        session_name=name,
        **comparison,
    )


def _ensure_session_gps_streams(session: models.Session) -> dict | None:
    """Fetch and store the session's Strava GPS streams if missing. Returns the activity detail (or None if streams were already stored)."""
    if session.gps_stream_json:
        return None

    client = StravaClient()
    try:
        activity = _find_strava_activity_for_session(client, session)
        if activity is None or activity.get("id") is None:
            raise HTTPException(
                status_code=404,
                detail=f"Could not find a Strava activity matching session {session.id} ({session.date}).",
            )
        detail = client.get_activity_by_id(int(activity["id"])).get("activity", {})
        streams = client.get_activity_gps_streams(int(activity["id"]))
    except StravaConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except StravaAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    if not streams.get("distance") or not streams.get("time"):
        raise HTTPException(status_code=400, detail="This Strava activity has no distance/time streams (likely no GPS).")

    session.gps_stream_json = json.dumps(streams, ensure_ascii=False)
    return detail if isinstance(detail, dict) else None


@router.post("/routes/from-session", response_model=schemas.RouteDetailResponse)
def create_route_from_session(payload: schemas.RouteMatchRequest, db: Session = Depends(get_db)):
    """Create a route directly from a Strava activity (analysis mode): the activity's GPS track becomes the route."""
    session = crud.get_session_by_id(db, payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {payload.session_id} not found")

    existing = db.query(models.Route).filter(models.Route.session_id == session.id, models.Route.gpx_xml.is_(None)).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A route already exists for this activity: \"{existing.name}\" (route {existing.id}).",
        )

    activity_detail = _ensure_session_gps_streams(session)
    if activity_detail is None:
        # streams were already stored; still try to get the activity description (non-fatal)
        try:
            client = StravaClient()
            activity = _find_strava_activity_for_session(client, session)
            if activity is not None and activity.get("id") is not None:
                activity_detail = client.get_activity_by_id(int(activity["id"])).get("activity")
        except (StravaConfigError, StravaAPIError, HTTPException):
            activity_detail = None

    streams = json.loads(session.gps_stream_json)

    try:
        processed = process_streams(streams)
    except GPXProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    name = None
    description = None
    if activity_detail:
        name = str(activity_detail.get("name") or "").strip() or None
        description = str(activity_detail.get("description") or "").strip() or None
    if not name and session.notes:
        name = str(session.notes).splitlines()[0].strip() or None
    if not description and session.notes:
        lines = str(session.notes).splitlines()
        description = "\n".join(lines[1:]).strip() or None
    if not name:
        name = f"{session.type} {session.date}"

    route = crud.create_route(
        db,
        name=name,
        source_filename=None,
        gpx_xml=None,
        track_json=json.dumps(processed["track"], ensure_ascii=False),
        distance_km=processed["distance_km"],
        elevation_gain_m=processed["elevation_gain_m"],
        elevation_loss_m=processed["elevation_loss_m"],
        min_elevation_m=processed["min_elevation_m"],
        max_elevation_m=processed["max_elevation_m"],
        has_elevation=processed["has_elevation"],
    )
    route.session_id = session.id
    if description:
        route.notes = description
    db.commit()
    db.refresh(route)

    return _route_detail_response(route, [])


@router.post("/routes/{route_id}/match-session", response_model=schemas.RouteComparisonResponse)
def match_route_session(route_id: int, payload: schemas.RouteMatchRequest, db: Session = Depends(get_db)):
    """Link a route to a session: finds the Strava activity, fetches its GPS streams, and returns the comparison."""
    route = crud.get_route(db, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    session = crud.get_session_by_id(db, payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {payload.session_id} not found")

    _ensure_session_gps_streams(session)

    route.session_id = session.id
    db.commit()
    db.refresh(route)
    db.refresh(session)

    return _build_comparison_response(route, session)


@router.get("/routes/{route_id}/comparison", response_model=schemas.RouteComparisonResponse)
def get_route_comparison(route_id: int, db: Session = Depends(get_db)):
    """Get the planned-vs-actual comparison for a route linked to a session."""
    route = crud.get_route(db, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    if route.session_id is None:
        raise HTTPException(status_code=404, detail="Route is not linked to a session")
    session = crud.get_session_by_id(db, route.session_id)
    if not session or not session.gps_stream_json:
        raise HTTPException(status_code=404, detail="Linked session has no stored GPS streams")
    return _build_comparison_response(route, session)


@router.delete("/routes/{route_id}/match-session")
def unlink_route_session(route_id: int, db: Session = Depends(get_db)):
    """Unlink a route from its session (keeps the session's stored streams)."""
    route = crud.get_route(db, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    route.session_id = None
    db.commit()
    return {"ok": True}


@router.post("/routes/{route_id}/markers", response_model=schemas.RouteMarkerResponse)
def create_route_marker(route_id: int, payload: schemas.RouteMarkerCreate, db: Session = Depends(get_db)):
    """Add a ravito or note marker anchored at distance_km; position is interpolated from the track."""
    route = crud.get_route(db, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")

    track = json.loads(route.track_json)
    point = interpolate_point_at_distance(track, payload.distance_km)

    return crud.create_route_marker(
        db,
        route_id=route_id,
        kind=payload.kind,
        distance_km=point["distance_km"],
        lat=point["lat"],
        lng=point["lng"],
        elevation_m=point["elevation_m"],
        label=payload.label,
        note=payload.note,
    )


@router.put("/routes/{route_id}/markers/{marker_id}", response_model=schemas.RouteMarkerResponse)
def update_route_marker(route_id: int, marker_id: int, payload: schemas.RouteMarkerUpdate, db: Session = Depends(get_db)):
    """Update a marker; re-interpolates lat/lng/elevation when distance_km changes."""
    route = crud.get_route(db, route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    marker = crud.get_route_marker(db, route_id, marker_id)
    if not marker:
        raise HTTPException(status_code=404, detail="Marker not found")

    data = payload.model_dump(exclude_unset=True)
    if "distance_km" in data and data["distance_km"] is not None:
        track = json.loads(route.track_json)
        point = interpolate_point_at_distance(track, data["distance_km"])
        marker.distance_km = point["distance_km"]
        marker.lat = point["lat"]
        marker.lng = point["lng"]
        marker.elevation_m = point["elevation_m"]
        data.pop("distance_km")
    for key, value in data.items():
        setattr(marker, key, value)
    db.commit()
    db.refresh(marker)
    return marker


@router.delete("/routes/{route_id}/markers/{marker_id}")
def delete_route_marker(route_id: int, marker_id: int, db: Session = Depends(get_db)):
    """Delete a marker."""
    if not crud.delete_route_marker(db, route_id, marker_id):
        raise HTTPException(status_code=404, detail="Marker not found")
    return {"ok": True}


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
