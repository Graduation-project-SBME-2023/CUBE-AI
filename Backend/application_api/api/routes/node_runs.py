"""Node-run routes"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.session import get_db
from application_api.repositories import node_runs as runs_repo
from application_api.schemas.node_runs import (
    AgentOutputWrite,
    NodeRunComplete,
    NodeRunFail,
    NodeRunOut,
    NodeRunStart,
    NodeRunTransitionOut,
    ScratchpadFlag,
    ScratchpadNote,
    ScratchpadRegister,
    ScratchpadSetStatus,
    ScratchpadSummary,
)

router = APIRouter(prefix="/runs", tags=["Runs"])


@router.get("/session/{session_id}", response_model=list[NodeRunOut])
async def list_session_runs(session_id: UUID, db: AsyncSession = Depends(get_db)):
    """Every run of a session's approved graph, in node order."""
    return await runs_repo.list_runs(db, session_id)


@router.get("/session/{session_id}/status")
async def session_run_status(session_id: UUID, db: AsyncSession = Depends(get_db)):
    """The `node_status` view, rebuilt from the runs themselves."""
    return runs_repo.node_status_payload(await runs_repo.list_runs(db, session_id))


@router.get("/session/{session_id}/scratchpad")
async def session_scratchpad(session_id: UUID, db: AsyncSession = Depends(get_db)):
    """The reporter's cross-agent scratchpad view (`scratchpad_read_report`)."""
    return runs_repo.scratchpad_report(await runs_repo.list_runs(db, session_id))


@router.get("/session/{session_id}/by-target/{target_name}")
async def resolve_run(
    session_id: UUID, target_name: str, db: AsyncSession = Depends(get_db)
):
    """The FIRST run in graph order whose node names this target."""
    run, matched = await runs_repo.resolve_run_for_target(db, session_id, target_name)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=f"No approved graph node in session {session_id} names {target_name!r}",
        )
    return {
        "run": NodeRunOut.model_validate(run).model_dump(mode="json"),
        "nodes_sharing_this_target": matched,
    }


@router.get("/{run_id}", response_model=NodeRunOut)
async def read_run(run_id: UUID, db: AsyncSession = Depends(get_db)):
    return await runs_repo.get_run(db, run_id)


@router.post("/{run_id}/start", response_model=NodeRunTransitionOut)
async def start_run(
    run_id: UUID, data: NodeRunStart, db: AsyncSession = Depends(get_db)
):
    """Claim a pending run, or reclaim an interrupted one."""
    run, event = await runs_repo.start_run(db, run_id, data)
    return {"run": run, "event": event}


@router.post("/{run_id}/complete", response_model=NodeRunTransitionOut)
async def complete_run(
    run_id: UUID, data: NodeRunComplete, db: AsyncSession = Depends(get_db)
):
    run, event = await runs_repo.complete_run(db, run_id, data)
    return {"run": run, "event": event}


@router.post("/{run_id}/fail", response_model=NodeRunTransitionOut)
async def fail_run(
    run_id: UUID, data: NodeRunFail, db: AsyncSession = Depends(get_db)
):
    run, event = await runs_repo.fail_run(db, run_id, data)
    return {"run": run, "event": event}


@router.post("/{run_id}/scratchpad/register", response_model=NodeRunOut)
async def scratchpad_register(
    run_id: UUID, data: ScratchpadRegister, db: AsyncSession = Depends(get_db)
):
    return await runs_repo.scratchpad_register(db, run_id, data)


@router.post("/{run_id}/scratchpad/notes", response_model=NodeRunOut)
async def scratchpad_note(
    run_id: UUID, data: ScratchpadNote, db: AsyncSession = Depends(get_db)
):
    return await runs_repo.scratchpad_write_note(db, run_id, data)


@router.post("/{run_id}/scratchpad/flags", response_model=NodeRunOut)
async def scratchpad_flag(
    run_id: UUID, data: ScratchpadFlag, db: AsyncSession = Depends(get_db)
):
    return await runs_repo.scratchpad_raise_flag(db, run_id, data)


@router.post("/{run_id}/scratchpad/status", response_model=NodeRunOut)
async def scratchpad_status(
    run_id: UUID, data: ScratchpadSetStatus, db: AsyncSession = Depends(get_db)
):
    return await runs_repo.scratchpad_set_status(db, run_id, data)


@router.post("/{run_id}/scratchpad/summary", response_model=NodeRunOut)
async def scratchpad_summary(
    run_id: UUID, data: ScratchpadSummary, db: AsyncSession = Depends(get_db)
):
    return await runs_repo.scratchpad_write_summary(db, run_id, data)


@router.put("/{run_id}/output", response_model=NodeRunOut)
async def write_output(
    run_id: UUID, data: AgentOutputWrite, db: AsyncSession = Depends(get_db)
):
    """`write_agent_output`. Replaces this run's own document; never another's."""
    return await runs_repo.write_agent_output(db, run_id, data)
