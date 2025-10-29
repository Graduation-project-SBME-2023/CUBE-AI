

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime
from monailabel.models import chat_sessions as models
from monailabel.schemas import chat_sessions as schemas


# ---------------------------
# CREATE CHAT SESSION
# ---------------------------
async def create_chat_session(db: AsyncSession, data: schemas.ChatSessionCreate):
    new_session = models.ChatSession(**data.dict())
    db.add(new_session)
    await db.commit()
    await db.refresh(new_session)
    return new_session


# ---------------------------
# GET CHAT SESSION BY ID
# ---------------------------
async def get_chat_session(db: AsyncSession, chat_id: int):
    result = await db.execute(
        select(models.ChatSession).where(models.ChatSession.chat_id == chat_id)
    )
    return result.scalar_one_or_none()


# ---------------------------
# GET ALL CHAT SESSIONS (optional filter)
# ---------------------------
async def get_chat_sessions(db: AsyncSession, study_UID: str = None, active_only: bool = False):
    query = select(models.ChatSession)
    if study_UID:
        query = query.where(models.ChatSession.study_UID == study_UID)
    if active_only:
        query = query.where(models.ChatSession.is_active == True)

    result = await db.execute(query)
    return result.scalars().all()


# ---------------------------
# UPDATE CHAT SESSION
# ---------------------------
async def update_chat_session(db: AsyncSession, chat_id: int, data: schemas.ChatSessionUpdate):
    result = await db.execute(
        select(models.ChatSession).where(models.ChatSession.chat_id == chat_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        return None

    # Update all fields provided in request
    for field, value in data.dict(exclude_unset=True).items():
        setattr(session, field, value)

    # Always refresh last_activity when any update happens
    session.last_activity = datetime.utcnow()

    await db.commit()
    await db.refresh(session)
    return session

# ---------------------------
# TOUCH CHAT SESSION (update only last_activity)
# ---------------------------
async def touch_chat_session(db: AsyncSession, chat_id: int):
    """Update only the last_activity timestamp of a chat session."""
    result = await db.execute(
        select(models.ChatSession).where(models.ChatSession.chat_id == chat_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        return None

    session.last_activity = datetime.utcnow()

    await db.commit()
    return session


# ---------------------------
# DEACTIVATE CHAT SESSION
# ---------------------------
async def deactivate_chat_session(db: AsyncSession, chat_id: int):
    result = await db.execute(
        select(models.ChatSession).where(models.ChatSession.chat_id == chat_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        return None

    session.is_active = False
    session.last_activity = datetime.utcnow()

    await db.commit()
    await db.refresh(session)
    return session


# ---------------------------
# ACTIVATE CHAT SESSION
# ---------------------------
async def activate_chat_session(db: AsyncSession, chat_id: int):
    result = await db.execute(
        select(models.ChatSession).where(models.ChatSession.chat_id == chat_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        return None

    session.is_active = True
    session.last_activity = datetime.utcnow()

    await db.commit()
    await db.refresh(session)
    return session


# ---------------------------
# DELETE CHAT SESSION
# ---------------------------
async def delete_chat_session(db: AsyncSession, chat_id: int):
    result = await db.execute(
        select(models.ChatSession).where(models.ChatSession.chat_id == chat_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        return False

    await db.delete(session)
    await db.commit()
    return True
