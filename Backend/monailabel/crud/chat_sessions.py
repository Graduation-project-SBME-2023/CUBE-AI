
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime
from models import chat_sessions as models
from models.study import Study
from schemas import chat_sessions as schemas

# ---------------------------
# CREATE CHAT SESSION
# ---------------------------
async def create_chat_session(db: AsyncSession, data: schemas.ChatSessionCreate):
    # Create the chat session (excluding study_UIDs)
    new_session = models.ChatSession(
        title=data.title,
        is_active=data.is_active,
        summary=data.summary,
    )

    # Link studies if provided
    if data.study_UIDs:
        studies = (await db.execute(
            select(Study).where(Study.study_UID.in_(data.study_UIDs))
        )).scalars().all()
        new_session.studies = studies

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

    # Filter by study_UID (through the association)
    if study_UID:
        query = query.join(models.ChatSession.studies).where(Study.study_UID == study_UID)
    if active_only:
        query = query.where(models.ChatSession.is_active == True)

    result = await db.execute(query)
    return result.scalars().unique().all()

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

    update_data = data.dict(exclude_unset=True)

    # Handle study reassignment separately
    if "study_UIDs" in update_data and update_data["study_UIDs"] is not None:
        studies = (await db.execute(
            select(Study).where(Study.study_UID.in_(update_data["study_UIDs"]))
        )).scalars().all()
        session.studies = studies
        del update_data["study_UIDs"]

    # Update other fields
    for field, value in update_data.items():
        setattr(session, field, value)

    # Always refresh last_activity when any update happens
    session.last_activity = datetime.utcnow()

    await db.commit()
    await db.refresh(session)
    return session

# ---------------------------
# TOUCH CHAT SESSION
# ---------------------------
async def touch_chat_session(db: AsyncSession, chat_id: int):
    result = await db.execute(
        select(models.ChatSession).where(models.ChatSession.chat_id == chat_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        return None

    session.last_activity = datetime.utcnow()
    await db.commit()
    await db.refresh(session)
    return session

# ---------------------------
# DEACTIVATE CHAT SESSION
# ---------------------------
async def deactivate_chat_session(db: AsyncSession, chat_id: int):
    session = await get_chat_session(db, chat_id)
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
    session = await get_chat_session(db, chat_id)
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
    session = await get_chat_session(db, chat_id)
    if not session:
        return False

    await db.delete(session)
    await db.commit()
    return True

# ---------------------------
# ADD NOTE TO SUMMARY
# ---------------------------
async def add_hypothesis_note(db: AsyncSession, chat_id: int, note: str):
    session = await get_chat_session(db, chat_id)
    if not session:
        return None

    if session.summary:
        session.summary += f"\n{note}"
    else:
        session.summary = note
    session.last_activity = datetime.utcnow()

    await db.commit()
    await db.refresh(session)
    return session
