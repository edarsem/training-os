from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from datetime import date, timedelta
from app.core.database import get_db
from app.schemas import schemas
from app.crud import crud

router = APIRouter()

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
