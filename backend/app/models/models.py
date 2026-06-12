from sqlalchemy import Column, Integer, String, Float, Date, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base

class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, index=True, nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=True)
    external_id = Column(String, unique=True, index=True, nullable=True)
    timezone_name = Column(String, nullable=True)
    type = Column(String, index=True, nullable=False) # run, trail, swim, bike, hike, skate, strength, mobility, other
    duration_minutes = Column(Integer, nullable=False)
    elapsed_duration_minutes = Column(Integer, nullable=True)
    moving_duration_minutes = Column(Integer, nullable=True)
    distance_km = Column(Float, nullable=True)
    elevation_gain_m = Column(Integer, nullable=True)
    average_pace_min_per_km = Column(Float, nullable=True)
    average_heart_rate_bpm = Column(Float, nullable=True)
    max_heart_rate_bpm = Column(Float, nullable=True)
    perceived_intensity = Column(Integer, nullable=True) # 1-10
    is_race = Column(Boolean, nullable=False, default=False)
    training_load = Column(Float, nullable=True)
    training_load_elapsed = Column(Float, nullable=True)
    hr_stream_json = Column(Text, nullable=True)
    gps_stream_json = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class SessionHRZoneTime(Base):
    __tablename__ = "session_hr_zone_time"

    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True)
    zone_0_seconds = Column(Integer, nullable=False, default=0)
    zone_1_seconds = Column(Integer, nullable=False, default=0)
    zone_2_seconds = Column(Integer, nullable=False, default=0)
    zone_3_seconds = Column(Integer, nullable=False, default=0)
    zone_4_seconds = Column(Integer, nullable=False, default=0)
    zone_5_seconds = Column(Integer, nullable=False, default=0)
    zone_6_seconds = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class DailyTrainingLoad(Base):
    __tablename__ = "daily_training_load"

    date = Column(Date, primary_key=True, index=True)
    load = Column(Float, nullable=False, default=0.0)
    atl = Column(Float, nullable=False, default=0.0)
    ctl = Column(Float, nullable=False, default=0.0)
    acwr = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class DayNote(Base):
    __tablename__ = "day_notes"

    date = Column(Date, primary_key=True, index=True)
    note = Column(Text, nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class WeeklyPlan(Base):
    __tablename__ = "weekly_plans"

    year = Column(Integer, primary_key=True)
    week_number = Column(Integer, primary_key=True)
    description = Column(Text, nullable=False)
    target_distance_km = Column(Float, nullable=True)
    target_sessions = Column(Integer, nullable=True)
    tags = Column(String, nullable=True) # comma separated
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Route(Base):
    __tablename__ = "routes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    notes = Column(Text, nullable=True)
    source_filename = Column(String, nullable=True)
    gpx_xml = Column(Text, nullable=True)
    track_json = Column(Text, nullable=False)
    distance_km = Column(Float, nullable=True)
    elevation_gain_m = Column(Float, nullable=True)
    elevation_loss_m = Column(Float, nullable=True)
    min_elevation_m = Column(Float, nullable=True)
    max_elevation_m = Column(Float, nullable=True)
    has_elevation = Column(Boolean, nullable=False, default=False)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    markers = relationship(
        "RouteMarker",
        back_populates="route",
        cascade="all, delete-orphan",
    )


class RouteMarker(Base):
    __tablename__ = "route_markers"

    id = Column(Integer, primary_key=True, index=True)
    route_id = Column(Integer, ForeignKey("routes.id", ondelete="CASCADE"), index=True, nullable=False)
    kind = Column(String, nullable=False)  # ravito | note
    distance_km = Column(Float, nullable=False)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    elevation_m = Column(Float, nullable=True)
    label = Column(String, nullable=True)
    note = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    route = relationship("Route", back_populates="markers")


class ChatConversation(Base):
    __tablename__ = "chat_conversations"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False, default="New chat")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    messages = relationship(
        "ChatMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("chat_conversations.id", ondelete="CASCADE"), index=True, nullable=False)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    conversation = relationship("ChatConversation", back_populates="messages")


class CoachMemoryItem(Base):
    __tablename__ = "coach_memory"

    id         = Column(Integer, primary_key=True, index=True)
    key        = Column(String(80), unique=True, nullable=False, index=True)
    value      = Column(String(200), nullable=False)
    source     = Column(String(20), nullable=False, default="coach")  # "coach" | "user"
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
