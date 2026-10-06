from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.api.dependencies import current_user, owned_session
from application_api.db.session import get_db
from application_api.repositories import sessions as crud
from application_api.schemas import sessions as schemas

router = APIRouter(prefix="/sessions", tags=["Sessions"])


@router.post("/", response_model=schemas.Session, status_code=status.HTTP_201_CREATED)
async def create_session(
    data: schemas.SessionCreate,
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """The session, its graph, its one chat and the opening request."""
    return await crud.create_session(db, data, user_id)


@router.get("/", response_model=list[schemas.SessionSummary])
async def list_sessions(
    control_state: str | None = None,
    include_archived: bool = False,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    return await crud.get_sessions(
        db, user_id, control_state=control_state, include_archived=include_archived, skip=skip, limit=limit
    )


@router.get("/{session_id}", response_model=schemas.Session)
async def read_session(
    session_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    return await crud.get_session(db, session_id, user_id)


@router.get("/{session_id}/activity")
async def read_activity(session_id: UUID = Depends(owned_session), db: AsyncSession = Depends(get_db)):
    """The derived activity: never stored."""
    return {"session_id": session_id, "activity": await crud.session_activity(db, session_id)}


@router.patch("/{session_id}", response_model=schemas.Session)
async def update_session(
    session_id: UUID,
    data: schemas.SessionUpdate,
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the free fields. `prompt`, the control state and the settings
    have their own rules and routes."""
    return await crud.update_session(db, session_id, data, user_id)


@router.patch("/{session_id}/settings", response_model=schemas.Session)
async def update_settings(
    session_id: UUID,
    data: schemas.SessionSettingsUpdate,
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """`execution_mode`, `privacy_mode`; a switch to phi_local also cancels the attempts that run under `standard`."""
    return await crud.update_settings(db, session_id, data, user_id)


@router.post("/{session_id}/inbox-cursor", response_model=schemas.Session)
async def advance_inbox(
    session_id: UUID,
    data: schemas.InboxAdvance,
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """The Main handled every waking event through this id."""
    return await crud.advance_inbox(db, session_id, data, user_id)


@router.patch("/{session_id}/control-state/{new_state}", response_model=schemas.Session)
async def set_session_control_state(
    session_id: UUID,
    new_state: str,
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Guarded control-intent transition: this is how pause is made durable."""
    return await crud.set_session_control_state(db, session_id, new_state, user_id)


@router.post("/{session_id}/archive", response_model=schemas.Session)
async def archive_session(
    session_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    """Normal removal."""
    return await crud.archive_session(db, session_id, user_id)


@router.delete("/{session_id}", status_code=status.HTTP_200_OK)
async def delete_session(
    session_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    """Hard delete. Refused while provenance still references the session — archive instead."""
    return await crud.delete_session(db, session_id, user_id)
