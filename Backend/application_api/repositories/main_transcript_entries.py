"""Main-transcript repository: the Main's saved Claude Agent SDK transcript.

One transcript per CUBE.AI session (its `sdk_session_id` is the CUBE.AI
`session_id`); the Composer and the Recovery Agent are its subpaths. Not an execution
table: the session's own runtime is the one writer (one Agent System worker),
and the routes check ownership. Entries are opaque and never a result.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.core.config import settings
from application_api.db.models.main_transcript_entries import MainTranscriptEntry
from application_api.schemas import agent_transcript_entries as schemas
from application_api.services.publication import conflict, database_refusal


def check_project_key(project_key: str) -> None:
    """The deployment's one project key, when it is configured (`AGENT_SESSION_PROJECT_KEY`)."""
    expected = settings.AGENT_SESSION_PROJECT_KEY
    if expected and project_key != expected:
        raise conflict(
            "PROJECT_KEY_MISMATCH",
            "The transcript names another deployment's project key; resume from the configured working directory.",
            status_code=422,
        )


async def append(db: AsyncSession, session_id: UUID, data: schemas.TranscriptAppend) -> schemas.TranscriptAppendOut:
    """One batch, in order; a replayed entry uuid is a no-op."""
    check_project_key(data.project_key)
    rows = [
        {"session_id": session_id, "subpath": data.subpath, "entry_uuid": entry["uuid"], "entry": entry}
        for entry in data.entries
    ]
    try:
        stored = (
            await db.execute(
                insert(MainTranscriptEntry.__table__)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["session_id", "subpath", "entry_uuid"])
                .returning(MainTranscriptEntry.__table__.c.seq)
            )
        ).all()
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise database_refusal(exc) from exc
    return schemas.TranscriptAppendOut(stored=len(stored), replayed=len(rows) - len(stored))


async def load(db: AsyncSession, session_id: UUID, subpath: str) -> Optional[list[dict]]:
    entries = (
        await db.execute(
            select(MainTranscriptEntry.entry)
            .where(MainTranscriptEntry.session_id == session_id, MainTranscriptEntry.subpath == subpath)
            .order_by(MainTranscriptEntry.seq)
        )
    ).scalars().all()
    return list(entries) or None


async def list_subpaths(db: AsyncSession, session_id: UUID) -> list[str]:
    return list(
        (
            await db.execute(
                select(MainTranscriptEntry.subpath)
                .where(MainTranscriptEntry.session_id == session_id, MainTranscriptEntry.subpath != "")
                .group_by(MainTranscriptEntry.subpath)
                .order_by(MainTranscriptEntry.subpath)
            )
        ).scalars()
    )


_ARCHIVED = text(
    """
    SELECT DISTINCT s.session_id
    FROM sessions s JOIN main_transcript_entries e ON e.session_id = s.session_id
    WHERE s.control_state = 'archived' AND s.updated_at <= now() - make_interval(days => :days)
    LIMIT :batch
    """
)


async def purge_archived(db: AsyncSession, days: int, batch: int) -> tuple[int, int]:
    """Inside the caller's transaction: the Main transcripts of sessions
    archived for longer than the retention (an archive is final, so its
    `updated_at` is the archive time). Returns (sessions, entries)."""
    sessions = list((await db.execute(_ARCHIVED, {"days": days, "batch": batch})).scalars())
    if not sessions:
        return 0, 0
    result = await db.execute(delete(MainTranscriptEntry).where(MainTranscriptEntry.session_id.in_(sessions)))
    return len(sessions), result.rowcount or 0
