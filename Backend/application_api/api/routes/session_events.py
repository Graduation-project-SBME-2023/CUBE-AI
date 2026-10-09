"""Session event routes"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from application_api.api.dependencies import current_user, owned_event, owned_session
from application_api.db.session import get_db
from application_api.schemas.session_events import SessionEventCreate, SessionEventOut
import application_api.repositories.session_events as events_crud
from application_api.repositories.users import require_owner

router = APIRouter(prefix="/session-events", tags=["Session events"])


@router.post("/", response_model=SessionEventOut, status_code=status.HTTP_201_CREATED)
async def create_session_event(
    data: SessionEventCreate,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(current_user),
):
    """Publish one event and return it with the `event_id` clients replay from."""
    await require_owner(db, data.session_id, user_id)
    return await events_crud.create_session_event(db, data)


@router.get("/", response_model=list[SessionEventOut])
async def get_all_events(
    after_event_id: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(current_user),
):
    """Events of the caller's sessions only."""
    return await events_crud.get_events(
        db, after_event_id=after_event_id, limit=limit, owner=user_id
    )


@router.get("/filter", response_model=list[SessionEventOut])
async def get_events(
    session_id: UUID | None = None,
    event_type: str | None = None,
    after_event_id: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(current_user),
):
    """Events of the caller's sessions only; a named session must be theirs."""
    if session_id is not None:
        await require_owner(db, session_id, user_id)
    return await events_crud.get_events(
        db,
        owner=user_id,
        session_id=session_id,
        event_type=event_type,
        after_event_id=after_event_id,
        limit=limit,
    )


@router.get("/session/{session_id}", response_model=list[SessionEventOut])
async def replay_session(
    session_id: UUID,
    after_event_id: int = Query(
        0, ge=0, description="Replay strictly after this event id (the client's cursor)"
    ),
    limit: int = Query(500, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    _: UUID = Depends(owned_session),
):
    """Replay a session's events in `event_id` order from a cursor."""
    return await events_crud.replay_session(
        db, session_id, after_event_id=after_event_id, limit=limit
    )


@router.get("/{event_id}", response_model=SessionEventOut)
async def get_session_event(
    event_id: int, db: AsyncSession = Depends(get_db), _: UUID = Depends(owned_event)
):
    return await events_crud.get_session_event(db, event_id)
