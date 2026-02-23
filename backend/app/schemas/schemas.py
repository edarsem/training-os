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
    elapsed_duration_minutes: Optional[int] = None
    moving_duration_minutes: Optional[int] = None
    distance_km: Optional[float] = None
    elevation_gain_m: Optional[int] = None
    average_pace_min_per_km: Optional[float] = None
    average_heart_rate_bpm: Optional[float] = None
    max_heart_rate_bpm: Optional[float] = None
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


class StravaActivityResponse(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    sport_type: Optional[str] = None
    start_date: Optional[datetime] = None
    moving_time_seconds: Optional[int] = None
    elapsed_time_seconds: Optional[int] = None
    distance_km: Optional[float] = None
    elevation_gain_m: Optional[float] = None


class StravaRateLimitsResponse(BaseModel):
    global_limit: Optional[str] = None
    global_usage: Optional[str] = None
    read_limit: Optional[str] = None
    read_usage: Optional[str] = None


class StravaRecentActivitiesResponse(BaseModel):
    attempted_limit: int
    fetched_count: int
    auto_refreshed_token: bool
    activities: List[StravaActivityResponse] = []
    rate_limits: StravaRateLimitsResponse


class StravaImportItemResponse(BaseModel):
    strava_activity_id: Optional[int] = None
    external_id: Optional[str] = None
    session_id: Optional[int] = None
    action: str
    mapped_type: Optional[str] = None
    session_date: Optional[date] = None
    name: Optional[str] = None


class StravaImportResponse(BaseModel):
    fetched_count: int
    checked_count: int = 0
    pages_fetched: int = 0
    stopped_on_existing: bool = False
    imported_count: int
    updated_count: int
    skipped_count: int
    auto_refreshed_token: bool
    items: List[StravaImportItemResponse] = []
