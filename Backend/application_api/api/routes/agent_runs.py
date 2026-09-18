"""Agent-run routes"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.session import get_db
from application_api.repositories import agent_runs as runs_repo
from application_api.schemas.agent_runs import (
    AgentOutputWrite,
    AgentRunComplete,
    AgentRunFail,
    AgentRunOut,
    AgentRunStart,
    AgentRunTransitionOut,
    ScratchpadFlag,
    ScratchpadNote,
    ScratchpadRegister,
    ScratchpadSetStatus,
    ScratchpadSummary,
)

router = APIRouter(prefix="/agent-runs", tags=["Agent Runs"])


@router.get("/session/{session_id}", response_model=list[AgentRunOut])
async def list_session_runs(session_id: UUID, db: AsyncSession = Depends(get_db)):
    """Every run of a session's approved plan, in step order."""
    return await runs_repo.list_runs(db, session_id)


@router.get("/session/{session_id}/status")
async def session_run_status(session_id: UUID, db: AsyncSession = Depends(get_db)):
    """The `pipeline_agent_status` view, rebuilt from the runs themselves."""
    return runs_repo.agent_status_payload(await runs_repo.list_runs(db, session_id))


@router.get("/session/{session_id}/scratchpad")
async def session_scratchpad(session_id: UUID, db: AsyncSession = Depends(get_db)):
    """The reporter's cross-agent scratchpad view (`scratchpad_read_report`)."""
    return runs_repo.scratchpad_report(await runs_repo.list_runs(db, session_id))


@router.get("/session/{session_id}/by-agent-type/{agent_type}")
async def resolve_run(
    session_id: UUID, agent_type: str, db: AsyncSession = Depends(get_db)
):
    """The FIRST run in plan order whose step names this agent type."""
    run, matched = await runs_repo.resolve_run_for_agent_type(db, session_id, agent_type)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=f"No approved plan step in session {session_id} names {agent_type!r}",
        )
    return {
        "run": AgentRunOut.model_validate(run).model_dump(mode="json"),
        "steps_sharing_this_agent_type": matched,
    }


@router.get("/{agent_run_id}", response_model=AgentRunOut)
async def read_run(agent_run_id: UUID, db: AsyncSession = Depends(get_db)):
    return await runs_repo.get_run(db, agent_run_id)


@router.post("/{agent_run_id}/start", response_model=AgentRunTransitionOut)
async def start_run(
    agent_run_id: UUID, data: AgentRunStart, db: AsyncSession = Depends(get_db)
):
    """Claim a pending run, or reclaim an interrupted one."""
    run, event = await runs_repo.start_run(db, agent_run_id, data)
    return {"run": run, "event": event}


@router.post("/{agent_run_id}/complete", response_model=AgentRunTransitionOut)
async def complete_run(
    agent_run_id: UUID, data: AgentRunComplete, db: AsyncSession = Depends(get_db)
):
    run, event = await runs_repo.complete_run(db, agent_run_id, data)
    return {"run": run, "event": event}


@router.post("/{agent_run_id}/fail", response_model=AgentRunTransitionOut)
async def fail_run(
    agent_run_id: UUID, data: AgentRunFail, db: AsyncSession = Depends(get_db)
):
    run, event = await runs_repo.fail_run(db, agent_run_id, data)
    return {"run": run, "event": event}


@router.post("/{agent_run_id}/scratchpad/register", response_model=AgentRunOut)
async def scratchpad_register(
    agent_run_id: UUID, data: ScratchpadRegister, db: AsyncSession = Depends(get_db)
):
    return await runs_repo.scratchpad_register(db, agent_run_id, data)


@router.post("/{agent_run_id}/scratchpad/notes", response_model=AgentRunOut)
async def scratchpad_note(
    agent_run_id: UUID, data: ScratchpadNote, db: AsyncSession = Depends(get_db)
):
    return await runs_repo.scratchpad_write_note(db, agent_run_id, data)


@router.post("/{agent_run_id}/scratchpad/flags", response_model=AgentRunOut)
async def scratchpad_flag(
    agent_run_id: UUID, data: ScratchpadFlag, db: AsyncSession = Depends(get_db)
):
    return await runs_repo.scratchpad_raise_flag(db, agent_run_id, data)


@router.post("/{agent_run_id}/scratchpad/status", response_model=AgentRunOut)
async def scratchpad_status(
    agent_run_id: UUID, data: ScratchpadSetStatus, db: AsyncSession = Depends(get_db)
):
    return await runs_repo.scratchpad_set_status(db, agent_run_id, data)


@router.post("/{agent_run_id}/scratchpad/summary", response_model=AgentRunOut)
async def scratchpad_summary(
    agent_run_id: UUID, data: ScratchpadSummary, db: AsyncSession = Depends(get_db)
):
    return await runs_repo.scratchpad_write_summary(db, agent_run_id, data)


@router.put("/{agent_run_id}/output", response_model=AgentRunOut)
async def write_output(
    agent_run_id: UUID, data: AgentOutputWrite, db: AsyncSession = Depends(get_db)
):
    """`write_agent_output`. Replaces this run's own document; never another's."""
    return await runs_repo.write_agent_output(db, agent_run_id, data)
