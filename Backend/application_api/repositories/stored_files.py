"""Stored-file repository"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.core.stored_file_limits import (
    KIND_MIME_TYPES,
    max_bytes_for,
)
from application_api.db.models.stored_files import StoredFile
from application_api.schemas.stored_files import RenderRegistration

logger = logging.getLogger(__name__)


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validate_kind_and_mime(kind: str, mime_type: str) -> None:
    allowed = KIND_MIME_TYPES.get(kind)
    if allowed is None:
        raise HTTPException(status_code=422, detail=f"Unknown stored file kind {kind!r}")
    if mime_type.split(";")[0].strip().lower() not in allowed:
        raise HTTPException(
            status_code=422,
            detail=(
                f"kind {kind!r} does not admit mime type {mime_type!r}; expected one "
                f"of {', '.join(allowed)}"
            ),
        )


async def _existing(
    db: AsyncSession, session_id: UUID, logical_key: str
) -> StoredFile | None:
    return (
        await db.execute(
            select(StoredFile).where(
                StoredFile.session_id == session_id,
                StoredFile.logical_key == logical_key,
            )
        )
    ).scalar_one_or_none()


def _reuse_or_conflict(existing: StoredFile, sha256: str) -> StoredFile:
    if existing.sha256 == sha256:
        return existing
    raise HTTPException(
        status_code=409,
        detail=(
            f"Stored file {existing.logical_key!r} already exists in this session with "
            f"different content (stored sha256 {existing.sha256[:12]}…, submitted "
            f"{sha256[:12]}…). Stored file bytes are immutable; publish a new "
            "logical_key instead of replacing them."
        ),
    )


async def store_bytes(
    db: AsyncSession,
    *,
    session_id: UUID,
    logical_key: str,
    kind: str,
    display_filename: str,
    mime_type: str,
    payload: bytes,
    created_by_run_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[StoredFile, bool]:
    """Store retained bytes. Returns (stored file, created)."""
    _validate_kind_and_mime(kind, mime_type)
    if not payload:
        raise HTTPException(
            status_code=422,
            detail="A stored file with zero bytes is a failed write, not a stored file.",
        )
    cap = max_bytes_for(kind)
    if len(payload) > cap:
        raise HTTPException(
            status_code=413,
            detail=f"{kind} stored files are capped at {cap} bytes; got {len(payload)}.",
        )

    sha256 = _digest(payload)
    existing = await _existing(db, session_id, logical_key)
    if existing is not None:
        return _reuse_or_conflict(existing, sha256), False

    stored_file = StoredFile(
        session_id=session_id,
        created_by_run_id=created_by_run_id,
        kind=kind,
        logical_key=logical_key,
        display_filename=display_filename,
        mime_type=mime_type,
        byte_size=len(payload),
        sha256=sha256,
        body=payload,
        render_path=None,
        metadata_=metadata,
        retention_class="retained",
    )
    db.add(stored_file)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # Lost the race to another writer with the same key: re-read and apply
        # the same reuse-or-conflict rule rather than failing the caller.
        existing = await _existing(db, session_id, logical_key)
        if existing is not None:
            return _reuse_or_conflict(existing, sha256), False
        raise HTTPException(status_code=409, detail=f"Stored file rejected: {exc}") from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    await db.refresh(stored_file)
    return stored_file, True


def resolve_render_path(session_id: UUID, render_path: str) -> Path:
    """Resolve a relative render key through `storage_paths`."""
    from storage_paths import workspace, workspace_subpath

    root = workspace(str(session_id))
    try:
        resolved = workspace_subpath(root, render_path)
        resolved.relative_to(Path(root).resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="render_path escapes the session workspace"
        ) from exc
    return resolved


async def register_render(
    db: AsyncSession, data: RenderRegistration
) -> tuple[StoredFile, bool]:
    """Catalogue a regenerable render that lives on disk."""
    _validate_kind_and_mime(data.kind, data.mime_type)
    resolved = resolve_render_path(data.session_id, data.render_path)
    if not resolved.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"No render exists at {data.render_path!r} for this session",
        )
    payload = resolved.read_bytes()
    if not payload:
        raise HTTPException(
            status_code=422, detail="A zero-byte render is a failed render."
        )
    cap = max_bytes_for(data.kind)
    if len(payload) > cap:
        raise HTTPException(
            status_code=413,
            detail=f"{data.kind} renders are capped at {cap} bytes; got {len(payload)}.",
        )
    sha256 = _digest(payload)

    existing = await _existing(db, data.session_id, data.logical_key)
    if existing is not None:
        if existing.retention_class != "render":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{data.logical_key!r} already exists in this session as retained "
                    "bytes; a render cannot take over a retained stored file's key."
                ),
            )
        if existing.render_path != data.render_path:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Render {data.logical_key!r} is catalogued at "
                    f"{existing.render_path!r}; catalogue a different render under a "
                    "different key rather than re-pointing this one."
                ),
            )
        if existing.sha256 == sha256:
            return existing, False
        # A render is a cache entry, not a promise. Regenerating it — the
        # user edited the mask — legitimately changes the bytes under the same
        # key, so the catalogue records the new digest instead of disagreeing
        # with the file. (Retained bytes are the opposite case and are
        # immutable, in the application and in a trigger.)
        existing.sha256 = sha256
        existing.byte_size = len(payload)
        existing.metadata_ = data.metadata or existing.metadata_
        try:
            await db.commit()
        except SQLAlchemyError as exc:
            await db.rollback()
            raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
        await db.refresh(existing)
        return existing, False

    stored_file = StoredFile(
        session_id=data.session_id,
        created_by_run_id=data.created_by_run_id,
        kind=data.kind,
        logical_key=data.logical_key,
        display_filename=data.display_filename or resolved.name,
        mime_type=data.mime_type,
        byte_size=len(payload),
        sha256=sha256,
        body=None,
        render_path=data.render_path,
        metadata_=data.metadata,
        retention_class="render",
    )
    db.add(stored_file)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        existing = await _existing(db, data.session_id, data.logical_key)
        if existing is not None:
            return existing, False
        raise HTTPException(status_code=409, detail=f"Render rejected: {exc}") from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    await db.refresh(stored_file)
    return stored_file, True


# ----------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------
_CATALOGUE_COLUMNS = (
    StoredFile.file_id,
    StoredFile.session_id,
    StoredFile.created_by_run_id,
    StoredFile.kind,
    StoredFile.logical_key,
    StoredFile.display_filename,
    StoredFile.mime_type,
    StoredFile.byte_size,
    StoredFile.sha256,
    StoredFile.render_path,
    StoredFile.metadata_,
    StoredFile.schema_version,
    StoredFile.retention_class,
    StoredFile.created_at,
)


async def list_stored_files(
    db: AsyncSession,
    session_id: UUID,
    kind: str | None = None,
    skip: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Catalogue rows only — **never** `SELECT *`."""
    query = select(*_CATALOGUE_COLUMNS).where(StoredFile.session_id == session_id)
    if kind:
        query = query.where(StoredFile.kind == kind)
    result = await db.execute(
        query.order_by(StoredFile.created_at, StoredFile.logical_key).offset(skip).limit(limit)
    )
    return [dict(row._mapping) for row in result]


async def get_stored_file(db: AsyncSession, file_id: UUID) -> dict[str, Any]:
    result = await db.execute(
        select(*_CATALOGUE_COLUMNS).where(StoredFile.file_id == file_id)
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Stored file {file_id} not found")
    return dict(row._mapping)


async def read_body(db: AsyncSession, file_id: UUID) -> tuple[dict[str, Any], bytes]:
    """The bytes, in one short transaction."""
    row = await get_stored_file(db, file_id)
    if row["retention_class"] == "render":
        resolved = resolve_render_path(row["session_id"], row["render_path"])
        if not resolved.is_file():
            raise HTTPException(
                status_code=404,
                detail=(
                    f"The render for {row['logical_key']!r} is not on disk. It is "
                    "regenerable: ask the producer to render it again."
                ),
            )
        return row, resolved.read_bytes()

    payload = (
        await db.execute(
            select(StoredFile.body).where(StoredFile.file_id == file_id)
        )
    ).scalar_one()
    if payload is None:
        raise HTTPException(
            status_code=500,
            detail=f"Stored file {file_id} is retained but has no bytes.",
        )
    return row, bytes(payload)
