from sqlalchemy.orm import Session as DBSession
from app.models import models
from app.schemas import schemas
from datetime import date
from typing import List, Optional

# --- Sessions ---
def get_sessions_by_date_range(db: DBSession, start_date: date, end_date: date) -> List[models.Session]:
    return db.query(models.Session).filter(
        models.Session.date >= start_date,
        models.Session.date <= end_date
    ).order_by(
        models.Session.date.asc(),
        models.Session.start_time.asc(),
        models.Session.id.asc()
    ).all()

def create_session(db: DBSession, session: schemas.SessionCreate) -> models.Session:
    db_session = models.Session(**session.model_dump())
    db.add(db_session)
    db.commit()
    db.refresh(db_session)
    return db_session

def update_session(db: DBSession, session_id: int, session: schemas.SessionUpdate) -> Optional[models.Session]:
    db_session = db.query(models.Session).filter(models.Session.id == session_id).first()
    if db_session:
        for key, value in session.model_dump().items():
            setattr(db_session, key, value)
        db.commit()
        db.refresh(db_session)
    return db_session

def delete_session(db: DBSession, session_id: int) -> bool:
    db_session = db.query(models.Session).filter(models.Session.id == session_id).first()
    if db_session:
        db.delete(db_session)
        db.commit()
        return True
    return False

# --- Day Notes ---
def get_day_note(db: DBSession, note_date: date) -> Optional[models.DayNote]:
    return db.query(models.DayNote).filter(models.DayNote.date == note_date).first()

def get_day_notes_by_date_range(db: DBSession, start_date: date, end_date: date) -> List[models.DayNote]:
    return db.query(models.DayNote).filter(
        models.DayNote.date >= start_date,
        models.DayNote.date <= end_date
    ).all()

def upsert_day_note(db: DBSession, note: schemas.DayNoteCreate) -> models.DayNote:
    db_note = get_day_note(db, note.date)
    if db_note:
        db_note.note = note.note
    else:
        db_note = models.DayNote(**note.model_dump())
        db.add(db_note)
    db.commit()
    db.refresh(db_note)
    return db_note

# --- Weekly Plans ---
def get_weekly_plan(db: DBSession, year: int, week_number: int) -> Optional[models.WeeklyPlan]:
    return db.query(models.WeeklyPlan).filter(
        models.WeeklyPlan.year == year,
        models.WeeklyPlan.week_number == week_number
    ).first()

def upsert_weekly_plan(db: DBSession, plan: schemas.WeeklyPlanCreate) -> models.WeeklyPlan:
    db_plan = get_weekly_plan(db, plan.year, plan.week_number)
    if db_plan:
        for key, value in plan.model_dump().items():
            setattr(db_plan, key, value)
    else:
        db_plan = models.WeeklyPlan(**plan.model_dump())
        db.add(db_plan)
    db.commit()
    db.refresh(db_plan)
    return db_plan


# --- Chat Conversations ---
def list_chat_conversations(db: DBSession, limit: int = 100) -> List[models.ChatConversation]:
    return db.query(models.ChatConversation).order_by(models.ChatConversation.updated_at.desc(), models.ChatConversation.id.desc()).limit(limit).all()


def get_chat_conversation(db: DBSession, conversation_id: int) -> Optional[models.ChatConversation]:
    return db.query(models.ChatConversation).filter(models.ChatConversation.id == conversation_id).first()


def create_chat_conversation(db: DBSession, title: Optional[str] = None) -> models.ChatConversation:
    value = (title or "New chat").strip() or "New chat"
    conversation = models.ChatConversation(title=value)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def delete_chat_conversation(db: DBSession, conversation_id: int) -> bool:
    conversation = get_chat_conversation(db, conversation_id)
    if not conversation:
        return False
    db.delete(conversation)
    db.commit()
    return True


def list_chat_messages(db: DBSession, conversation_id: int) -> List[models.ChatMessage]:
    return (
        db.query(models.ChatMessage)
        .filter(models.ChatMessage.conversation_id == conversation_id)
        .order_by(models.ChatMessage.created_at.asc(), models.ChatMessage.id.asc())
        .all()
    )


def create_chat_message(db: DBSession, conversation_id: int, role: str, content: str) -> models.ChatMessage:
    message = models.ChatMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
    )
    db.add(message)

    conversation = get_chat_conversation(db, conversation_id)
    if conversation:
        if role == "user" and (conversation.title or "").strip() in {"", "New chat"}:
            first_line = (content or "").splitlines()[0].strip() if content else ""
            conversation.title = first_line[:80] if first_line else "New chat"

    db.commit()
    db.refresh(message)
    return message
