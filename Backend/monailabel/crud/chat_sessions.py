from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime
from fastapi import HTTPException
from models import chat_sessions as models
from models.study import Study
from schemas import chat_sessions as schemas

def ensure_last_activity(session):
    if session and session.last_activity is None:
        session.last_activity = session.started_at
    return session

# ---------------------------
# CREATE CHAT SESSION
# ---------------------------
async def create_chat_session(db: AsyncSession, data: schemas.ChatSessionCreate):
    try:
        new_session = models.ChatSession(
            title=data.title,
            is_active=data.is_active,
            summary=data.summary,
        )

        if data.study_UIDs:
            studies = (await db.execute(
                select(Study).where(Study.study_uid.in_(data.study_UIDs))
            )).scalars().all()
            new_session.studies = studies

        db.add(new_session)
        await db.commit()
        await db.refresh(new_session)
        return ensure_last_activity(new_session)
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

# ---------------------------
# GET CHAT SESSION BY ID
# ---------------------------
async def get_chat_session(db: AsyncSession, chat_id: int):
    try:
        result = await db.execute(
            select(models.ChatSession).where(models.ChatSession.chat_id == chat_id)
        )
        row = result.unique().one_or_none()
        session = row[0] if row else None
        if not session:
            raise HTTPException(status_code=404, detail=f"Chat session {chat_id} not found")
        return ensure_last_activity(session)
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# ---------------------------
# GET ALL CHAT SESSIONS
# ---------------------------
async def get_chat_sessions(db: AsyncSession, study_UID: str = None, active_only: bool = False):
    try:
        query = select(models.ChatSession)

        # Filter by study_UID (through the association)
        if study_UID:
            query = query.join(models.ChatSession.studies).where(Study.study_uid == study_UID)
        if active_only:
            query = query.where(models.ChatSession.is_active == True)

        result = await db.execute(query)
        # Use unique() first, then extract ChatSession objects manually
        sessions = [row[0] for row in result.unique().all()]  
        return [ensure_last_activity(s) for s in sessions]
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# ---------------------------
# UPDATE CHAT SESSION
# ---------------------------
async def update_chat_session(db: AsyncSession, chat_id: int, data: schemas.ChatSessionUpdate):
    try:
        result = await db.execute(
            select(models.ChatSession).where(models.ChatSession.chat_id == chat_id)
        )
        row = result.unique().one_or_none()
        session = row[0] if row else None
        if not session:
            raise HTTPException(status_code=404, detail=f"Chat session {chat_id} not found")

        update_data = data.dict(exclude_unset=True)

        if "study_UIDs" in update_data and update_data["study_UIDs"] is not None:
            studies = (await db.execute(
                select(Study).where(Study.study_uid.in_(update_data["study_UIDs"]))
            )).scalars().all()
            session.studies = studies
            del update_data["study_UIDs"]

        for field, value in update_data.items():
            setattr(session, field, value)

        session.last_activity = datetime.utcnow()
        await db.commit()
        await db.refresh(session)
        return session
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# ---------------------------
# TOUCH CHAT SESSION
# ---------------------------
async def touch_chat_session(db: AsyncSession, chat_id: int):
    try:
        session = await get_chat_session(db, chat_id)
        if not session:
            raise HTTPException(status_code=404, detail=f"Chat session {chat_id} not found")
        session.last_activity = datetime.utcnow()
        await db.commit()
        await db.refresh(session)
        return session
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# ---------------------------
# DEACTIVATE CHAT SESSION
# ---------------------------
async def deactivate_chat_session(db: AsyncSession, chat_id: int):
    try:
        session = await get_chat_session(db, chat_id)
        if not session:
            raise HTTPException(status_code=404, detail=f"Chat session {chat_id} not found")
        session.is_active = False
        session.last_activity = datetime.utcnow()
        await db.commit()
        await db.refresh(session)
        return session
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# ---------------------------
# ACTIVATE CHAT SESSION
# ---------------------------
async def activate_chat_session(db: AsyncSession, chat_id: int):
    try:
        session = await get_chat_session(db, chat_id)
        if not session:
            raise HTTPException(status_code=404, detail=f"Chat session {chat_id} not found")
        session.is_active = True
        session.last_activity = datetime.utcnow()
        await db.commit()
        await db.refresh(session)
        return session
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# ---------------------------
# DELETE CHAT SESSION
# ---------------------------
async def delete_chat_session(db: AsyncSession, chat_id: int):
    try:
        session = await get_chat_session(db, chat_id)
        if not session:
            raise HTTPException(status_code=404, detail=f"Chat session {chat_id} not found")
        await db.delete(session)
        await db.commit()
        return {"detail": f"Chat session {chat_id} deleted successfully"}
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete chat session: {str(e)}")

# ---------------------------
# ADD NOTE TO SUMMARY
# ---------------------------
async def add_hypothesis_note(db: AsyncSession, chat_id: int, note: str):
    try:
        session = await get_chat_session(db, chat_id)
        if not session:
            raise HTTPException(status_code=404, detail=f"Chat session {chat_id} not found")
        if session.summary:
            session.summary += f"\n{note}"
        else:
            session.summary = note
        session.last_activity = datetime.utcnow()
        await db.commit()
        await db.refresh(session)
        return session
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
