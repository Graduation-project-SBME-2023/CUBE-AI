"""Stored-file routes"""

from __future__ import annotations

import asyncio
import json
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.core.stored_file_limits import (
    BINARY_READ_QUEUE_TIMEOUT_SECONDS,
    BINARY_READ_RETRY_AFTER_SECONDS,
    max_bytes_for,
)
from application_api.db.binary_session import binary_read_slots, get_binary_db
from application_api.api.dependencies import current_user, owned_file, owned_file_content, owned_session
from application_api.db.session import get_db
from application_api.services.fencing import fenced_db
from application_api.repositories.users import require_owner
from application_api.repositories import stored_files as repo
from application_api.schemas.stored_files import RenderRegistration, StoredFileOut

router = APIRouter(prefix="/files", tags=["Stored files"])


@router.post("/", response_model=StoredFileOut, status_code=201)
async def upload_stored_file(
    session_id: UUID = Form(...),
    logical_key: str = Form(...),
    kind: str = Form(...),
    display_filename: Optional[str] = Form(None),
    created_by_run_id: Optional[UUID] = Form(None),
    metadata: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(fenced_db),
    user_id: UUID = Depends(current_user),
):
    """Publish retained bytes."""
    await require_owner(db, session_id, user_id)
    payload = await file.read()
    cap = max_bytes_for(kind)
    if len(payload) > cap:
        raise HTTPException(
            status_code=413,
            detail=f"{kind} stored files are capped at {cap} bytes; got {len(payload)}.",
        )
    parsed_metadata = None
    if metadata:
        try:
            parsed_metadata = json.loads(metadata)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=422, detail=f"metadata is not valid JSON: {exc}"
            ) from exc

    stored_file, created = await repo.store_bytes(
        db,
        session_id=session_id,
        logical_key=logical_key,
        kind=kind,
        display_filename=display_filename or file.filename or logical_key,
        mime_type=file.content_type or "application/octet-stream",
        payload=payload,
        created_by_run_id=created_by_run_id,
        metadata=parsed_metadata,
    )
    return StoredFileOut.model_validate(stored_file)


@router.post("/renders", response_model=StoredFileOut, status_code=201)
async def register_render(
    data: RenderRegistration,
    db: AsyncSession = Depends(fenced_db),
    user_id: UUID = Depends(current_user),
):
    """Catalogue a regenerable render that already exists on disk."""
    await require_owner(db, data.session_id, user_id)
    stored_file, created = await repo.register_render(db, data)
    return StoredFileOut.model_validate(stored_file)


@router.get("/session/{session_id}", response_model=list[StoredFileOut])
async def list_session_stored_files(
    session_id: UUID,
    kind: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: UUID = Depends(owned_session),
):
    """The catalogue. Bytes are fetched one at a time from `/content`."""
    rows = await repo.list_stored_files(db, session_id, kind=kind, skip=skip, limit=limit)
    return [StoredFileOut.model_validate(row) for row in rows]


@router.get("/{file_id}", response_model=StoredFileOut)
async def read_stored_file(
    file_id: UUID, db: AsyncSession = Depends(get_db), _: UUID = Depends(owned_file)
):
    return StoredFileOut.model_validate(await repo.get_stored_file(db, file_id))


@router.get("/{file_id}/content")
async def read_stored_file_content(
    file_id: UUID,
    download: bool = Query(False, description="Send as an attachment"),
    db: AsyncSession = Depends(get_binary_db),
    _: UUID = Depends(owned_file_content),
):
    """Stream one stored file's bytes on the bounded binary pool."""
    try:
        await asyncio.wait_for(
            binary_read_slots.acquire(), timeout=BINARY_READ_QUEUE_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="Too many stored file downloads in flight; retry shortly.",
            headers={"Retry-After": str(BINARY_READ_RETRY_AFTER_SECONDS)},
        )
    try:
        row, payload = await repo.read_body(db, file_id)
    finally:
        binary_read_slots.release()

    disposition = "attachment" if download else "inline"
    return Response(
        content=payload,
        media_type=row["mime_type"],
        headers={
            "Content-Disposition": f'{disposition}; filename="{row["display_filename"]}"',
            "Content-Length": str(len(payload)),
            "ETag": f'"{row["sha256"]}"',
            # Bytes are immutable, so a fetched stored file never needs
            # revalidating — this is the one caching claim the schema can make
            # honestly.
            "Cache-Control": "private, max-age=31536000, immutable",
        },
    )
