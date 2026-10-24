"""Agent-transcript repository: the store behind `HttpSessionStore`.

An agent node keeps one Claude Agent SDK transcript per run; the SDK's session
id (`sdk_session_id`) is the run id. Writes come only from the run's latest attempt while it is running or
suspended (the attempt path, checked by the fencing trigger); any attempt of
the run reads, and so does the first attempt of a run seeded from it. Entries
are opaque: stored and returned unchanged, never parsed. The purge, on the
harness path, is the only delete.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.core.config import settings
from application_api.db.models.agent_transcript_entries import AgentTranscriptEntry
from application_api.db.models.node_runs import NodeRun
from application_api.db.models.run_attempts import RunAttempt
from application_api.repositories import main_transcript_entries as main_repo
from application_api.repositories.main_transcript_entries import check_project_key
from application_api.schemas import agent_transcript_entries as schemas
from application_api.services.fencing import HARNESS, WritePath, fenced_transaction
from application_api.services.publication import conflict, database_refusal

logger = logging.getLogger(__name__)


async def _session_of(db: AsyncSession, run_id: UUID) -> UUID:
    session_id = (await db.execute(select(NodeRun.session_id).where(NodeRun.run_id == run_id))).scalar()
    if session_id is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return session_id


# ----------------------------------------------------------------------
# Writes (attempt path)
# ----------------------------------------------------------------------
async def append(
    db: AsyncSession, run_id: UUID, attempt_id: UUID, data: schemas.TranscriptAppend
) -> schemas.TranscriptAppendOut:
    """One batch, in order. A replayed entry uuid is a no-op (the SDK retries a
    batch whose first delivery may have landed)."""
    check_project_key(data.project_key)
    session_id = await _session_of(db, run_id)
    rows = [
        {"run_id": run_id, "subpath": data.subpath, "session_id": session_id, "attempt_id": attempt_id,
         "entry_uuid": entry["uuid"], "entry": entry}
        for entry in data.entries
    ]
    async with fenced_transaction(db, WritePath.attempt(attempt_id)):
        try:
            stored = (
                await db.execute(
                    insert(AgentTranscriptEntry.__table__)
                    .values(rows)
                    .on_conflict_do_nothing(index_elements=["run_id", "subpath", "entry_uuid"])
                    .returning(AgentTranscriptEntry.__table__.c.seq)
                )
            ).all()
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise database_refusal(exc) from exc
    return schemas.TranscriptAppendOut(stored=len(stored), replayed=len(rows) - len(stored))


# ----------------------------------------------------------------------
# Reads (any attempt of the run, or the first attempt of its seeded run)
# ----------------------------------------------------------------------
async def check_reader(db: AsyncSession, run_id: UUID, reader_attempt_id: Optional[UUID]) -> None:
    """A reading attempt belongs to the run, or is the first attempt of a run
    whose frozen `seed_from` names it (D27). A read that presents no attempt is
    an owner-checked read (the step timeline, the Recovery Agent)."""
    if reader_attempt_id is None:
        return
    reader = (
        await db.execute(
            select(RunAttempt.run_id, RunAttempt.attempt_number, NodeRun.execution_policy)
            .join(NodeRun, NodeRun.run_id == RunAttempt.run_id)
            .where(RunAttempt.attempt_id == reader_attempt_id)
        )
    ).one_or_none()
    if reader is not None:
        seed = (reader.execution_policy or {}).get("seed_from") or {}
        if reader.run_id == run_id or (reader.attempt_number == 1 and seed.get("run_id") == str(run_id)):
            return
    raise conflict(
        "TRANSCRIPT_FORBIDDEN",
        f"Attempt {reader_attempt_id} reads neither its own run's transcript nor its seed's.",
        status_code=403,
    )


async def load(db: AsyncSession, run_id: UUID, subpath: str) -> Optional[list[dict]]:
    """The entries in load order; None when the run has none under this subpath."""
    entries = (
        await db.execute(
            select(AgentTranscriptEntry.entry)
            .where(AgentTranscriptEntry.run_id == run_id, AgentTranscriptEntry.subpath == subpath)
            .order_by(AgentTranscriptEntry.seq)
        )
    ).scalars().all()
    return list(entries) or None


async def list_subpaths(db: AsyncSession, run_id: UUID) -> list[str]:
    """The run's subagent transcripts (`SessionStore.list_subkeys`)."""
    return list(
        (
            await db.execute(
                select(AgentTranscriptEntry.subpath)
                .where(AgentTranscriptEntry.run_id == run_id, AgentTranscriptEntry.subpath != "")
                .group_by(AgentTranscriptEntry.subpath)
                .order_by(AgentTranscriptEntry.subpath)
            )
        ).scalars()
    )


async def has_message(db: AsyncSession, run_id: UUID, message_uuid: str) -> bool:
    """Whether the run's main transcript holds this message (a seed's check)."""
    count = (
        await db.execute(
            select(func.count())
            .select_from(AgentTranscriptEntry)
            .where(
                AgentTranscriptEntry.run_id == run_id,
                AgentTranscriptEntry.subpath == "",
                AgentTranscriptEntry.entry_uuid == message_uuid,
            )
        )
    ).scalar_one()
    return count > 0


# ----------------------------------------------------------------------
# Retention (harness path)
# ----------------------------------------------------------------------
# Runs terminal for longer than the retention whose transcript no live
# seed_from names: the trigger's own rule (agent_transcripts_require_purgeable),
# selected here so the purge never trips it.
_PURGEABLE = text(
    """
    SELECT DISTINCT r.run_id
    FROM node_runs r JOIN agent_transcript_entries e ON e.run_id = r.run_id
    WHERE r.completed_at IS NOT NULL
      AND r.completed_at <= now() - make_interval(days => :days)
      AND NOT EXISTS (
          SELECT 1 FROM graph_changes g
          WHERE g.session_id = r.session_id
            AND jsonb_path_exists(g.content, '$.** ? (@.seed_from.run_id == $run)',
                                  jsonb_build_object('run', r.run_id::text))
            AND (g.status = 'pending' OR EXISTS (
                  SELECT 1 FROM graph_nodes n
                  WHERE n.accepted_by_change_id = g.change_id AND n.removed_at IS NULL
                    AND NOT EXISTS (SELECT 1 FROM node_runs s
                                    WHERE s.node_id = n.node_id
                                      AND s.accepted_by_change_id = g.change_id))))
    LIMIT :batch
    """
)


async def purge(db: AsyncSession, batch: int = 500) -> schemas.TranscriptPurgeOut:
    """One batch of the retention purge: agent transcripts of runs terminal for
    longer than AGENT_TRANSCRIPT_RETENTION_DAYS that no live seed_from names,
    then the Main transcripts of sessions archived for longer than it."""
    days = settings.AGENT_TRANSCRIPT_RETENTION_DAYS
    async with fenced_transaction(db, HARNESS):
        await db.execute(
            text("SELECT set_config('cubeai.transcript_retention_days', :days, true)"), {"days": str(days)}
        )
        runs = list((await db.execute(_PURGEABLE, {"days": days, "batch": batch})).scalars())
        deleted = 0
        if runs:
            result = await db.execute(delete(AgentTranscriptEntry).where(AgentTranscriptEntry.run_id.in_(runs)))
            deleted = result.rowcount or 0
        sessions, main_deleted = await main_repo.purge_archived(db, days, batch)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise database_refusal(exc) from exc
    if runs or sessions:
        logger.info(
            "Purged %d transcript entries of %d run(s) and %d Main entries of %d archived session(s) "
            "older than %d days", deleted, len(runs), main_deleted, sessions, days,
        )
    return schemas.TranscriptPurgeOut(runs=len(runs), entries=deleted + main_deleted, main_sessions=sessions)
