"""Main-transcript routes: the Main's saved Claude Agent SDK session (D21),
one per CUBE.AI session, with the Composer and the Recovery Agent as subpaths.
Owner-checked; the session's own runtime is the one writer."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.api.dependencies import owned_session
from application_api.db.session import get_db
from application_api.repositories import main_transcript_entries as repo
from application_api.schemas.agent_transcript_entries import TranscriptAppend, TranscriptAppendOut, TranscriptOut

router = APIRouter(prefix="/sessions", tags=["Main transcript"])


@router.post("/{session_id}/main-transcript", response_model=TranscriptAppendOut)
async def append_main_transcript(
    data: TranscriptAppend, session_id: UUID = Depends(owned_session), db: AsyncSession = Depends(get_db)
):
    """Append one batch; a replayed entry uuid is a no-op."""
    return await repo.append(db, session_id, data)


@router.get("/{session_id}/main-transcript", response_model=TranscriptOut)
async def load_main_transcript(
    subpath: str = Query("", max_length=512),
    session_id: UUID = Depends(owned_session), db: AsyncSession = Depends(get_db),
):
    """The entries in order, or 404 when the session has none under this subpath."""
    entries = await repo.load(db, session_id, subpath)
    if entries is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} has no Main transcript at {subpath!r}")
    return {"subpath": subpath, "entries": entries}


@router.get("/{session_id}/main-transcript/subpaths", response_model=list[str])
async def main_transcript_subpaths(session_id: UUID = Depends(owned_session), db: AsyncSession = Depends(get_db)):
    """The Main's subagent transcripts (the Composer, the Recovery Agent)."""
    return await repo.list_subpaths(db, session_id)
