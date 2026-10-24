"""Agent-transcript routes: the store behind the Agent System's
`HttpSessionStore`.

An agent node's run keeps its Claude Agent SDK transcript here; the SDK's
session id (`sdk_session_id`) is the run id. The run's latest attempt, running or suspended, appends (it presents
itself in `X-Attempt-Id`); a read may present the reading attempt, which must
belong to the run or be the first attempt of a run seeded from it.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.api.dependencies import owned_run
from application_api.db.session import get_db
from application_api.repositories import agent_transcript_entries as repo
from application_api.schemas.agent_transcript_entries import (
    TranscriptAppend,
    TranscriptAppendOut,
    TranscriptOut,
    TranscriptPurgeOut,
)
from application_api.services.fencing import WritePath, declared_write_path

router = APIRouter(tags=["Agent transcripts"])


@router.post("/runs/{run_id}/transcript", response_model=TranscriptAppendOut)
async def append_transcript(
    run_id: UUID, data: TranscriptAppend,
    path: Optional[WritePath] = Depends(declared_write_path),
    _: UUID = Depends(owned_run), db: AsyncSession = Depends(get_db),
):
    """Append one batch (attempt path). A replayed entry uuid is a no-op."""
    if path is None or path.attempt_id is None:
        raise HTTPException(status_code=403, detail="A transcript write presents its attempt in X-Attempt-Id.")
    return await repo.append(db, run_id, path.attempt_id, data)


@router.get("/runs/{run_id}/transcript", response_model=TranscriptOut)
async def load_transcript(
    run_id: UUID, subpath: str = Query("", max_length=512),
    path: Optional[WritePath] = Depends(declared_write_path),
    _: UUID = Depends(owned_run), db: AsyncSession = Depends(get_db),
):
    """The entries in order, or 404 when the run has none under this subpath."""
    await repo.check_reader(db, run_id, path.attempt_id if path else None)
    entries = await repo.load(db, run_id, subpath)
    if entries is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} has no transcript at subpath {subpath!r}")
    return {"subpath": subpath, "entries": entries}


@router.get("/runs/{run_id}/transcript/subpaths", response_model=list[str])
async def transcript_subpaths(run_id: UUID, _: UUID = Depends(owned_run), db: AsyncSession = Depends(get_db)):
    """The run's subagent transcripts."""
    return await repo.list_subpaths(db, run_id)


@router.post("/agent-transcripts/purge", response_model=TranscriptPurgeOut)
async def purge_transcripts(batch: int = Query(500, ge=1, le=5000), db: AsyncSession = Depends(get_db)):
    """Retention purge (harness path), one batch: the transcripts of runs
    terminal for longer than AGENT_TRANSCRIPT_RETENTION_DAYS that no live
    seed_from names, and the Main transcripts of sessions archived as long. The
    lifespan sweep (`services/retention.py`) runs it on a timer."""
    return await repo.purge(db, batch)
