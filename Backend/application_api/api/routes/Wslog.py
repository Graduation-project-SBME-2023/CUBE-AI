"""Session event routes"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from application_api.db.session import get_db
from application_api.schemas.Wslog import WSLog, WSLogCreate
import application_api.repositories.Wslog as wslog_crud

router = APIRouter(prefix="/ws_logs", tags=["WS Logs"])


@router.post("/", response_model=WSLog, status_code=status.HTTP_201_CREATED)
async def create_ws_log(data: WSLogCreate, db: AsyncSession = Depends(get_db)):
    """Publish one event and return it with the `event_id` clients replay from."""
    return await wslog_crud.create_ws_log(db, data)


@router.get("/", response_model=list[WSLog])
async def get_all_logs(
    after_event_id: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    return await wslog_crud.get_logs(db, after_event_id=after_event_id, limit=limit)


@router.get("/filter", response_model=list[WSLog])
async def get_logs(
    session_id: UUID | None = None,
    msg_type: str | None = None,
    after_event_id: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    return await wslog_crud.get_logs(
        db,
        session_id=session_id,
        msg_type=msg_type,
        after_event_id=after_event_id,
        limit=limit,
    )


@router.get("/session/{session_id}", response_model=list[WSLog])
async def replay_session(
    session_id: UUID,
    after_event_id: int = Query(
        0, ge=0, description="Replay strictly after this event id (the client's cursor)"
    ),
    limit: int = Query(500, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
):
    """Replay a session's events in `event_id` order from a cursor."""
    return await wslog_crud.replay_session(
        db, session_id, after_event_id=after_event_id, limit=limit
    )


@router.get("/{event_id}", response_model=WSLog)
async def get_ws_log(event_id: int, db: AsyncSession = Depends(get_db)):
    return await wslog_crud.get_ws_log(db, event_id)
