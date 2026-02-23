from pydantic import BaseModel, Field
from datetime import date, datetime
from typing import Optional, List

# --- Session Schemas ---
class SessionBase(BaseModel):
    date: date
    start_time: Optional[datetime] = None
    external_id: Optional[str] = None
    type: str = Field(..., description="run, trail, swim, bike, hike, skate, strength, mobility, other")
    duration_minutes: int
    distance_km: Optional[float] = None
    elevation_gain_m: Optional[int] = None
    perceived_intensity: Optional[int] = Field(None, ge=1, le=10)
    notes: Optional[str] = None

class SessionCreate(SessionBase):
    pass

class SessionUpdate(SessionBase):
    pass

class SessionResponse(SessionBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

# --- DayNote Schemas ---
class DayNoteBase(BaseModel):
    date: date
    note: str

class DayNoteCreate(DayNoteBase):
    pass

class DayNoteUpdate(BaseModel):
    note: str

class DayNoteResponse(DayNoteBase):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

# --- WeeklyPlan Schemas ---
class WeeklyPlanBase(BaseModel):
    year: int
    week_number: int
    description: str
    target_distance_km: Optional[float] = None
    target_sessions: Optional[int] = None
    tags: Optional[str] = None

class WeeklyPlanCreate(WeeklyPlanBase):
    pass

class WeeklyPlanUpdate(BaseModel):
    description: Optional[str] = None
    target_distance_km: Optional[float] = None
    target_sessions: Optional[int] = None
    tags: Optional[str] = None

class WeeklyPlanResponse(WeeklyPlanBase):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

# --- Aggregated Responses ---
class WeekSummaryResponse(BaseModel):
    year: int
    week_number: int
    plan: Optional[WeeklyPlanResponse] = None
    sessions: List[SessionResponse] = []
    day_notes: List[DayNoteResponse] = []
    total_duration_minutes: int = 0
    total_distance_km: float = 0.0
    total_elevation_gain_m: int = 0
