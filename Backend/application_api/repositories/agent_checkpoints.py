"""Agent-checkpoint repository: the store behind `HttpCheckpointSaver`.

Writes come only from the run's current running or suspended attempt (the
attempt path, checked by the fencing trigger); any attempt of the run reads.
The purge, on the harness path, is the only delete.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.core.config import settings
from application_api.db.models.agent_checkpoints import AgentCheckpoint, AgentCheckpointWrite
from application_api.db.models.node_runs import NodeRun
from application_api.schemas import agent_checkpoints as schemas
from application_api.services.fencing import HARNESS, WritePath, fenced_transaction
from application_api.services.publication import conflict, database_refusal

logger = logging.getLogger(__name__)


async def _session_of(db: AsyncSession, run_id: UUID) -> UUID:
    session_id = (await db.execute(select(NodeRun.session_id).where(NodeRun.run_id == run_id))).scalar()
    if session_id is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return session_id


async def _commit(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise database_refusal(exc) from exc


# ----------------------------------------------------------------------
# Writes (attempt path)
# ----------------------------------------------------------------------
async def put_checkpoint(
    db: AsyncSession, run_id: UUID, attempt_id: UUID, data: schemas.CheckpointPut
) -> AgentCheckpoint:
    """Store one checkpoint. A replay of the same checkpoint is a no-op; the
    same key with other content is a conflict (checkpoints are immutable)."""
    session_id = await _session_of(db, run_id)
    key = (run_id, data.checkpoint_ns, data.checkpoint_id)
    async with fenced_transaction(db, WritePath.attempt(attempt_id)):
        try:
            await db.execute(
                insert(AgentCheckpoint.__table__)
                .values(
                    run_id=run_id, session_id=session_id, checkpoint_ns=data.checkpoint_ns,
                    checkpoint_id=data.checkpoint_id, parent_checkpoint_id=data.parent_checkpoint_id,
                    attempt_id=attempt_id, type=data.type, checkpoint=data.checkpoint,
                    metadata=data.metadata,
                )
                .on_conflict_do_nothing(index_elements=["run_id", "checkpoint_ns", "checkpoint_id"])
            )
        except IntegrityError as exc:
            await db.rollback()
            raise database_refusal(exc) from exc
        stored = await db.get(AgentCheckpoint, key, populate_existing=True)
        if (stored.type, stored.checkpoint, stored.metadata_) != (data.type, data.checkpoint, data.metadata):
            await db.rollback()
            raise conflict(
                "CHECKPOINT_EXISTS",
                f"Checkpoint {data.checkpoint_id} of run {run_id} already exists with other content.",
            )
        await _commit(db)
    return stored


async def put_checkpoint_writes(
    db: AsyncSession, run_id: UUID, attempt_id: UUID, data: schemas.CheckpointWritesPut
) -> int:
    """Store the pending writes of one checkpoint; a replayed key is a no-op."""
    session_id = await _session_of(db, run_id)
    async with fenced_transaction(db, WritePath.attempt(attempt_id)):
        try:
            for write in data.writes:
                await db.execute(
                    insert(AgentCheckpointWrite.__table__)
                    .values(
                        run_id=run_id, session_id=session_id, checkpoint_ns=data.checkpoint_ns,
                        checkpoint_id=data.checkpoint_id, task_id=write.task_id, idx=write.idx,
                        channel=write.channel, type=write.type, value=write.value,
                        attempt_id=attempt_id,
                    )
                    .on_conflict_do_nothing()
                )
        except IntegrityError as exc:
            await db.rollback()
            raise database_refusal(exc) from exc
        await _commit(db)
    return len(data.writes)


# ----------------------------------------------------------------------
# Reads (any attempt of the run)
# ----------------------------------------------------------------------
async def _writes(db: AsyncSession, checkpoint: AgentCheckpoint) -> list[AgentCheckpointWrite]:
    return list(
        (
            await db.execute(
                select(AgentCheckpointWrite)
                .where(
                    AgentCheckpointWrite.run_id == checkpoint.run_id,
                    AgentCheckpointWrite.checkpoint_ns == checkpoint.checkpoint_ns,
                    AgentCheckpointWrite.checkpoint_id == checkpoint.checkpoint_id,
                )
                .order_by(AgentCheckpointWrite.task_id, AgentCheckpointWrite.idx)
            )
        ).scalars()
    )


async def get_checkpoint(
    db: AsyncSession, run_id: UUID, checkpoint_ns: str, checkpoint_id: Optional[str] = None
) -> schemas.CheckpointOut:
    """One checkpoint with its pending writes; the latest when no id is given."""
    query = select(AgentCheckpoint).where(
        AgentCheckpoint.run_id == run_id, AgentCheckpoint.checkpoint_ns == checkpoint_ns
    )
    if checkpoint_id is None:
        query = query.order_by(AgentCheckpoint.created_at.desc(), AgentCheckpoint.checkpoint_id.desc()).limit(1)
    else:
        query = query.where(AgentCheckpoint.checkpoint_id == checkpoint_id)
    checkpoint = (await db.execute(query)).scalar_one_or_none()
    if checkpoint is None:
        raise HTTPException(status_code=404, detail=f"No checkpoint of run {run_id} matches")
    out = schemas.CheckpointOut.model_validate(checkpoint)
    out.writes = [schemas.CheckpointWriteOut.model_validate(write) for write in await _writes(db, checkpoint)]
    return out


async def list_checkpoints(
    db: AsyncSession, run_id: UUID, checkpoint_ns: Optional[str], before: Optional[str], limit: int
) -> list[AgentCheckpoint]:
    """Newest first. `before` is a checkpoint id; LangGraph ids sort by time."""
    query = select(AgentCheckpoint).where(AgentCheckpoint.run_id == run_id)
    if checkpoint_ns is not None:
        query = query.where(AgentCheckpoint.checkpoint_ns == checkpoint_ns)
    if before is not None:
        query = query.where(AgentCheckpoint.checkpoint_id < before)
    query = query.order_by(AgentCheckpoint.created_at.desc(), AgentCheckpoint.checkpoint_id.desc()).limit(limit)
    return list((await db.execute(query)).scalars())


async def run_steps(db: AsyncSession, run_id: UUID) -> list[schemas.RunStep]:
    """The step timeline from checkpoint metadata, oldest first (no blobs)."""
    rows = (
        await db.execute(
            select(
                AgentCheckpoint.checkpoint_ns, AgentCheckpoint.checkpoint_id, AgentCheckpoint.attempt_id,
                AgentCheckpoint.metadata_, AgentCheckpoint.created_at,
            )
            .where(AgentCheckpoint.run_id == run_id)
            .order_by(AgentCheckpoint.created_at, AgentCheckpoint.checkpoint_id)
        )
    ).all()
    return [
        schemas.RunStep(
            checkpoint_ns=row.checkpoint_ns,
            checkpoint_id=row.checkpoint_id,
            attempt_id=row.attempt_id,
            step=row.metadata_.get("step"),
            node=row.metadata_.get("node"),
            tool_calls=row.metadata_.get("tool_calls", []),
            pending_tools=row.metadata_.get("pending_tools", []),
            created_at=row.created_at,
        )
        for row in rows
    ]


# ----------------------------------------------------------------------
# Retention (harness path)
# ----------------------------------------------------------------------
# Runs terminal for longer than the retention whose checkpoints no live
# seed_from names: the trigger's own rule (agent_checkpoints_require_purgeable),
# selected here so the purge never trips it.
_PURGEABLE = text(
    """
    SELECT DISTINCT r.run_id
    FROM node_runs r JOIN agent_checkpoints c ON c.run_id = r.run_id
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


async def purge_checkpoints(db: AsyncSession, batch: int = 500) -> schemas.PurgeOut:
    days = settings.AGENT_CHECKPOINT_RETENTION_DAYS
    async with fenced_transaction(db, HARNESS):
        await db.execute(
            text("SELECT set_config('cubeai.checkpoint_retention_days', :days, true)"), {"days": str(days)}
        )
        runs = list((await db.execute(_PURGEABLE, {"days": days, "batch": batch})).scalars())
        deleted = 0
        if runs:
            # Writes go with their checkpoint (ON DELETE CASCADE).
            result = await db.execute(delete(AgentCheckpoint).where(AgentCheckpoint.run_id.in_(runs)))
            deleted = result.rowcount or 0
        await _commit(db)
    if runs:
        logger.info("Purged %d checkpoint(s) of %d run(s) older than %d days", deleted, len(runs), days)
    return schemas.PurgeOut(runs=len(runs), checkpoints=deleted)
