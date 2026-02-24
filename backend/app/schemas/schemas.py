from enum import Enum
from pydantic import BaseModel, Field
from datetime import date, datetime
from typing import Any, Optional, List

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


class LLMContextLevel(str, Enum):
    session = "session"
    day = "day"
    week = "week"
    multi_week = "multi_week"
    block = "block"


class LLMInterpretRequest(BaseModel):
    query: str = Field(..., min_length=1)
    levels: List[LLMContextLevel] = Field(default_factory=lambda: [LLMContextLevel.week])
    language: Optional[str] = None

    anchor_year: Optional[int] = None
    anchor_week: Optional[int] = None
    date_start: Optional[date] = None
    date_end: Optional[date] = None
    multi_week_count: int = Field(default=4, ge=1, le=24)

    include_salient_sessions: bool = True
    salient_distance_km_threshold: float = Field(default=15.0, ge=0)
    salient_duration_minutes_threshold: int = Field(default=90, ge=1)
    max_sessions_per_level: int = Field(default=50, ge=1, le=500)

    generic_prompt_key: Optional[str] = None
    private_prompt_key: Optional[str] = None

    provider: Optional[str] = None
    model: Optional[str] = None
    deterministic: bool = True
    include_context_in_response: bool = True

    tool_hints: List[str] = Field(default_factory=list)


class LLMAuditResponse(BaseModel):
    generated_at_utc: datetime
    provider: str
    model: str
    language: str
    deterministic: bool
    levels: List[str]
    window: dict[str, Any] = Field(default_factory=dict)
    prompt_generic_key: str
    prompt_generic_path: str
    prompt_private_key: Optional[str] = None
    prompt_private_path: Optional[str] = None
    tool_hints: List[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)


class LLMInterpretResponse(BaseModel):
    answer: str
    context: Optional[dict[str, Any]] = None
    audit: LLMAuditResponse
