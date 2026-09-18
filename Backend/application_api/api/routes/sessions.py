from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.session import get_db
from application_api.repositories import sessions as crud
from application_api.schemas import sessions as schemas

router = APIRouter(prefix="/sessions", tags=["Sessions"])


@router.post("/", response_model=schemas.Session, status_code=status.HTTP_201_CREATED)
async def create_session(
    data: schemas.SessionCreate, db: AsyncSession = Depends(get_db)
):
    """Create a session from the user's own words."""
    return await crud.create_session(db, data)


@router.get("/", response_model=list[schemas.SessionSummary])
async def list_sessions(
    status_filter: str | None = Query(None, alias="status"),
    include_archived: bool = False,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await crud.get_sessions(
        db,
        status=status_filter,
        include_archived=include_archived,
        skip=skip,
        limit=limit,
    )


@router.get("/{session_id}", response_model=schemas.Session)
async def read_session(session_id: UUID, db: AsyncSession = Depends(get_db)):
    return await crud.get_session(db, session_id)


@router.patch("/{session_id}", response_model=schemas.Session)
async def update_session(
    session_id: UUID, data: schemas.SessionUpdate, db: AsyncSession = Depends(get_db)
):
    """Update the mutable session fields. `prompt` is not one of them."""
    return await crud.update_session(db, session_id, data)


@router.patch("/{session_id}/status/{new_status}", response_model=schemas.Session)
async def set_session_status(
    session_id: UUID, new_status: str, db: AsyncSession = Depends(get_db)
):
    """Guarded status transition — this is how pause is made durable."""
    return await crud.set_session_status(db, session_id, new_status)


@router.post("/{session_id}/archive", response_model=schemas.Session)
async def archive_session(session_id: UUID, db: AsyncSession = Depends(get_db)):
    """Normal removal."""
    return await crud.archive_session(db, session_id)


@router.delete("/{session_id}", status_code=status.HTTP_200_OK)
async def delete_session(session_id: UUID, db: AsyncSession = Depends(get_db)):
    """Hard delete. Refused while provenance still references the session —
    archive instead."""
    return await crud.delete_session(db, session_id)
