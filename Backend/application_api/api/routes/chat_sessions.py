"""DEPRECATED alias for the legacy `/chat_sessions` surface."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.session import get_db
from application_api.repositories import chats as chats_crud
from application_api.repositories import sessions as crud
from application_api.schemas import sessions as schemas

router = APIRouter(
    prefix="/chat_sessions",
    tags=["Chat Sessions (deprecated)"],
    deprecated=True,
)


class LegacyChatSessionCreate(BaseModel):
    prompt: str = Field(min_length=1)
    prompt_kind: schemas.PromptKind = "hypothesis"
    title: str | None = None


def _project(session, chat_id=None) -> dict:
    """Project a Session onto the old response shape."""
    return {
        "chat_id": session.session_id,
        "session_id": session.session_id,
        "default_chat_id": chat_id,
        "title": session.title,
        "prompt": session.prompt,
        "prompt_kind": session.prompt_kind,
        "status": session.status,
        "started_at": session.created_at,
        "created_at": session.created_at,
        "last_activity": session.updated_at,
        "messages": [],
    }


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_chat_session_alias(
    data: LegacyChatSessionCreate, db: AsyncSession = Depends(get_db)
):
    """Create a session plus one default chat, and return both ids."""
    session = await crud.create_session(
        db,
        schemas.SessionCreate(
            prompt=data.prompt,
            prompt_kind=data.prompt_kind,
            title=data.title,
        ),
    )
    chat = await chats_crud.create_chat(
        db, session.session_id, title=data.title or "Chat"
    )
    return _project(session, chat.chat_id)


@router.get("/")
async def list_chat_sessions_alias(
    active_only: bool = False,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    sessions = await crud.get_sessions(
        db,
        include_archived=not active_only,
        skip=skip,
        limit=limit,
    )
    return [_project(session) for session in sessions]


@router.get("/{session_id}")
async def get_chat_session_alias(session_id: UUID, db: AsyncSession = Depends(get_db)):
    return _project(await crud.get_session(db, session_id))


@router.delete("/{session_id}", status_code=status.HTTP_200_OK)
async def delete_chat_session_alias(
    session_id: UUID, db: AsyncSession = Depends(get_db)
):
    """Archives, which is what current means by removing a session."""
    session = await crud.archive_session(db, session_id)
    return {"detail": f"Session {session.session_id} archived"}
