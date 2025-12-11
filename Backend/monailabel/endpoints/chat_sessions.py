

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from schemas import chat_sessions as schemas
from crud import chat_sessions as crud
from core.database import get_db
from uuid import uuid4, UUID
router = APIRouter(prefix="/chat_sessions", tags=["Chat Sessions"])

# ---------------------------------------------------------
# CREATE A CHAT SESSION
# ---------------------------------------------------------
@router.post("/", response_model=schemas.ChatSession, status_code=status.HTTP_201_CREATED)
async def create_chat_session(data: schemas.ChatSessionCreate, db: AsyncSession = Depends(get_db)):
    """
    Create a new chat session and optionally link it to one or more study UIDs.
    """
    session = await crud.create_chat_session(db, data)
    # eager load relationships to avoid MissingGreenlet
    await db.refresh(session)
    return session


# ---------------------------------------------------------
# GET ALL CHAT SESSIONS
# ---------------------------------------------------------

@router.get("/", response_model=list[schemas.ChatSession])
async def get_chat_sessions(
    study_UID: str | None = None,
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve all chat sessions.
    Optionally filter by:
    - study_UID (get sessions related to a specific study)
    - active_only=True (get only active sessions)
    """
    return await crud.get_chat_sessions(db, study_UID=study_UID, active_only=active_only)
# ---------------------------------------------------------
# GET CHAT SESSION BY ID
# ---------------------------------------------------------
@router.get("/{chat_id}", response_model=schemas.ChatSession)
async def get_chat_session(chat_id: UUID, db: AsyncSession = Depends(get_db)):
    """
    Retrieve a chat session by its unique chat_id.
    """
    session = await crud.get_chat_session(db, chat_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return session


# ---------------------------------------------------------
# UPDATE CHAT SESSION (title, summary, etc.)
# ---------------------------------------------------------
@router.put("/{chat_id}", response_model=schemas.ChatSession)
async def update_chat_session(chat_id: UUID, data: schemas.ChatSessionUpdate, db: AsyncSession = Depends(get_db)):
    """
    Update chat session fields such as title, summary, or is_active.
    Automatically updates last_activity timestamp.
    """
    session = await crud.update_chat_session(db, chat_id, data)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return session

# ---------------------------------------------------------
# TOUCH CHAT SESSION (update only last_activity)
# ---------------------------------------------------------
@router.patch("/{chat_id}/touch", response_model=schemas.ChatSession)
async def touch_chat_session(chat_id: UUID, db: AsyncSession = Depends(get_db)):
    """
    Update only the last_activity timestamp of a chat session.
    Typically used when the user sends or receives a message.
    """
    session = await crud.touch_chat_session(db, chat_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return session

# ---------------------------------------------------------
# DEACTIVATE CHAT SESSION
# ---------------------------------------------------------
@router.patch("/{chat_id}/deactivate", response_model=schemas.ChatSession)
async def deactivate_chat_session(chat_id: UUID, db: AsyncSession = Depends(get_db)):
    """
    Mark a chat session as inactive (e.g., when user closes the session).
    """
    session = await crud.deactivate_chat_session(db, chat_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return session

# ---------------------------------------------------------
# ACTIVATE CHAT SESSION
# ---------------------------------------------------------
@router.patch("/{chat_id}/activate", response_model=schemas.ChatSession)
async def activate_chat_session(chat_id: UUID, db: AsyncSession = Depends(get_db)):
    """
    Reactivate a chat session when user resumes chatting.
    """
    session = await crud.activate_chat_session(db, chat_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return session

# ---------------------------------------------------------
# DELETE CHAT SESSION
# ---------------------------------------------------------
@router.delete("/{chat_id}", status_code=status.HTTP_200_OK)
async def delete_chat_session(chat_id: UUID, db: AsyncSession = Depends(get_db)):
    """
    Delete a chat session permanently.
    """
    deleted = await crud.delete_chat_session(db, chat_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return None
