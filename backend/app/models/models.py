from sqlalchemy import Column, Integer, String, Float, Date, Text, DateTime
from sqlalchemy.sql import func
from app.core.database import Base

class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, index=True, nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=True)
    external_id = Column(String, unique=True, index=True, nullable=True)
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
    notes = Column(Text, nullable=True)
    
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
