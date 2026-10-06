from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.api.dependencies import owned_session
from application_api.db.session import get_db
from application_api.schemas.chat_messages import ChatMessage as ChatMessageOut
from application_api.repositories import chats as crud
from application_api.schemas import chats as schemas

router = APIRouter(prefix="/chats", tags=["Chats"])


@router.post(
    "/session/{session_id}",
    response_model=schemas.Chat,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat(
    data: schemas.ChatCreate,
    session_id: UUID = Depends(owned_session),
    db: AsyncSession = Depends(get_db),
):
    """A session has one chat, created with it: this returns that chat."""
    return await crud.create_chat(db, session_id, title=data.title)


@router.post("/session/{session_id}/input", response_model=ChatMessageOut, status_code=status.HTTP_201_CREATED)
async def accept_input(
    data: schemas.InputAccept,
    response: Response,
    session_id: UUID = Depends(owned_session),
    db: AsyncSession = Depends(get_db),
):
    """The user's message and its durable `user_input` event, together.
    Called by the Agent System, which queues the message only after this
    returns. A replay of the same message id answers 200 with the stored row."""
    message, created = await crud.accept_input(db, session_id, data)
    if not created:
        response.status_code = status.HTTP_200_OK
    return message


@router.get("/session/{session_id}", response_model=list[schemas.Chat])
async def list_chats_for_session(
    session_id: UUID = Depends(owned_session),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await crud.get_chats_for_session(db, session_id, skip=skip, limit=limit)


@router.get("/", response_model=list[schemas.Chat])
async def list_chats(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await crud.list_chats(db, skip=skip, limit=limit)


@router.get("/{chat_id}", response_model=schemas.Chat)
async def read_chat(chat_id: UUID, db: AsyncSession = Depends(get_db)):
    return await crud.get_chat(db, chat_id)


@router.patch("/{chat_id}", response_model=schemas.Chat)
async def update_chat(
    chat_id: UUID, data: schemas.ChatUpdate, db: AsyncSession = Depends(get_db)
):
    return await crud.update_chat(db, chat_id, data.title)


@router.patch("/{chat_id}/touch", response_model=schemas.Chat)
async def touch_chat(chat_id: UUID, db: AsyncSession = Depends(get_db)):
    """Update only `last_activity_at` — used when a message is sent or received."""
    return await crud.touch_chat(db, chat_id)


@router.delete("/{chat_id}", status_code=status.HTTP_200_OK)
async def delete_chat(chat_id: UUID, db: AsyncSession = Depends(get_db)):
    return await crud.delete_chat(db, chat_id)
