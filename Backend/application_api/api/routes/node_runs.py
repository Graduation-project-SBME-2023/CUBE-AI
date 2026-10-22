"""Run routes: starting a node, the attempt lifecycle (dispatch, reconcile,
complete, cancel, suspend, answer and resume, pending tools), the reads, and
the agent scratchpad. The agent transcripts are in `agent_transcript_entries`.

Writes an attempt makes about itself (it marks itself running, completes or
fails, sets its pending tools, writes its scratchpad) present the attempt in
`X-Attempt-Id`. An answer is the user's. The other transitions are the harness's.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.api.dependencies import current_user, owned_node, owned_run, owned_session
from application_api.db.session import get_db
from application_api.repositories import node_runs as runs_repo
from application_api.repositories import run_attempts as attempts_repo
from application_api.schemas import run_attempts as attempt_schemas
from application_api.schemas.node_runs import (
    AgentOutputWrite,
    NodeRunOut,
    NodeRunWithAttemptsOut,
    NodeStart,
    NodeStartOut,
    ScratchpadFlag,
    ScratchpadNote,
    ScratchpadRegister,
    ScratchpadSetStatus,
    ScratchpadSummary,
)
from application_api.services.fencing import (
    WritePath,
    declared_write_path,
    fenced_db,
    require_attempt_header,
)

router = APIRouter(prefix="/runs", tags=["Runs"])

AttemptOut = attempt_schemas.RunAttemptOut


# ----------------------------------------------------------------------
# Start a node
# ----------------------------------------------------------------------
@router.post("/start", response_model=NodeStartOut, status_code=201)
async def start_node(
    data: NodeStart, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    """First start (run + attempt 1, inputs pinned) or a retry attempt by
    the retry rules. A refusal (409 NOT_READY, STALE_DEFINITION, ATTEMPT_ACTIVE,
    ...) means: re-read the graph."""
    await owned_node(data.node_id, user_id, db)
    run, attempt, first = await runs_repo.start_node(db, data)
    return {"run": run, "attempt": attempt, "first_start": first}


# ----------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------
@router.get("/session/{session_id}", response_model=list[NodeRunOut])
async def list_session_runs(session_id: UUID = Depends(owned_session), db: AsyncSession = Depends(get_db)):
    """The current run of every live node, in node order."""
    return await runs_repo.list_runs(db, session_id)


@router.get("/session/{session_id}/status")
async def session_run_status(session_id: UUID = Depends(owned_session), db: AsyncSession = Depends(get_db)):
    """The `node_status` view: every live node with its current run."""
    return await runs_repo.node_status_payload(db, session_id)


@router.get("/session/{session_id}/scratchpad")
async def session_scratchpad(session_id: UUID = Depends(owned_session), db: AsyncSession = Depends(get_db)):
    """The reporter's cross-agent scratchpad view (`scratchpad_read_report`)."""
    return runs_repo.scratchpad_report(await runs_repo.list_runs(db, session_id))


@router.get("/session/{session_id}/by-target/{target_name}")
async def resolve_run(
    target_name: str, session_id: UUID = Depends(owned_session), db: AsyncSession = Depends(get_db)
):
    """The FIRST current run, in node order, of a node with this target."""
    run, matched = await runs_repo.resolve_run_for_target(db, session_id, target_name)
    if run is None:
        raise HTTPException(
            status_code=404, detail=f"No started node in session {session_id} names {target_name!r}"
        )
    return {"run": NodeRunOut.model_validate(run).model_dump(mode="json"), "nodes_sharing_this_target": matched}


@router.get("/node/{node_id}", response_model=list[NodeRunWithAttemptsOut])
async def node_run_history(node_id: UUID, _: UUID = Depends(owned_node), db: AsyncSession = Depends(get_db)):
    """Every run of a node, superseded ones included, oldest first."""
    return await runs_repo.list_node_runs(db, node_id)


@router.get("/{run_id}", response_model=NodeRunWithAttemptsOut)
async def read_run(run_id: UUID, _: UUID = Depends(owned_run), db: AsyncSession = Depends(get_db)):
    """A run with its attempts."""
    return await runs_repo.get_run(db, run_id)


# ----------------------------------------------------------------------
# The attempt lifecycle
# ----------------------------------------------------------------------
_ATTEMPT = "/{run_id}/attempts/{attempt_id}"


@router.post(f"{_ATTEMPT}/dispatched", response_model=AttemptOut)
async def attempt_dispatched(
    run_id: UUID, attempt_id: UUID, data: attempt_schemas.AttemptDispatched,
    _: UUID = Depends(owned_run), db: AsyncSession = Depends(get_db),
):
    """Dispatch: `running` before an in-process spawn, `submitted` after a job send."""
    return await attempts_repo.mark_dispatched(db, run_id, attempt_id, data)


@router.post(f"{_ATTEMPT}/running", response_model=AttemptOut)
async def attempt_running(
    run_id: UUID, attempt_id: UUID,
    path: Optional[WritePath] = Depends(declared_write_path),
    _: UUID = Depends(owned_run), db: AsyncSession = Depends(get_db),
):
    """A job worker moves its own attempt to running before it writes anything."""
    require_attempt_header(path, attempt_id)
    return await attempts_repo.mark_running(db, run_id, attempt_id)


@router.post(f"{_ATTEMPT}/complete", response_model=AttemptOut)
async def attempt_complete(
    run_id: UUID, attempt_id: UUID, data: attempt_schemas.AttemptComplete,
    path: Optional[WritePath] = Depends(declared_write_path),
    _: UUID = Depends(owned_run), db: AsyncSession = Depends(get_db),
):
    """Success: `outputs` checked against the table allow-list; expectations."""
    require_attempt_header(path, attempt_id)
    return await attempts_repo.complete_attempt(db, run_id, attempt_id, data)


@router.post(f"{_ATTEMPT}/fail", response_model=AttemptOut)
async def attempt_fail(
    run_id: UUID, attempt_id: UUID, data: attempt_schemas.AttemptFail,
    path: Optional[WritePath] = Depends(declared_write_path),
    _: UUID = Depends(owned_run), db: AsyncSession = Depends(get_db),
):
    """Failure."""
    require_attempt_header(path, attempt_id)
    return await attempts_repo.fail_attempt(db, run_id, attempt_id, data)


@router.post(f"{_ATTEMPT}/cancel", response_model=AttemptOut)
async def attempt_cancel(
    run_id: UUID, attempt_id: UUID, data: attempt_schemas.AttemptCancel,
    _: UUID = Depends(owned_run), db: AsyncSession = Depends(get_db),
):
    """Cancel request (user Stop, Watchdog timeout). A suspended attempt is
    confirmed stopped at once."""
    return await attempts_repo.request_cancel(db, run_id, attempt_id, data)


@router.post(f"{_ATTEMPT}/stopped", response_model=AttemptOut)
async def attempt_stopped(
    run_id: UUID, attempt_id: UUID, data: attempt_schemas.AttemptStopped,
    _: UUID = Depends(owned_run), db: AsyncSession = Depends(get_db),
):
    """Cancel confirmation: the task or job was observed stopped."""
    return await attempts_repo.confirm_stopped(db, run_id, attempt_id, data)


@router.post(f"{_ATTEMPT}/reconcile", response_model=attempt_schemas.ReconcileOut)
async def attempt_reconcile(
    run_id: UUID, attempt_id: UUID, data: attempt_schemas.AttemptReconcile,
    _: UUID = Depends(owned_run), db: AsyncSession = Depends(get_db),
):
    """Reconcile: record what the backend reports after a restart."""
    attempt, action = await attempts_repo.reconcile_attempt(db, run_id, attempt_id, data)
    return {"attempt": attempt, "action": action}


@router.post(f"{_ATTEMPT}/suspend", response_model=AttemptOut)
async def attempt_suspend(
    run_id: UUID, attempt_id: UUID, data: attempt_schemas.AttemptSuspend,
    _: UUID = Depends(owned_run), db: AsyncSession = Depends(get_db),
):
    """T10 suspend: an `ask_user` question, a tool approval or a pause (v2)."""
    return await attempts_repo.suspend_attempt(db, run_id, attempt_id, data)


@router.post(f"{_ATTEMPT}/answer", response_model=AttemptOut)
async def attempt_answer(
    run_id: UUID, attempt_id: UUID, data: attempt_schemas.AttemptAnswer,
    _: UUID = Depends(owned_run), db: AsyncSession = Depends(get_db),
):
    """T10 answer (user only): the answer to an `ask_user` question or the
    decision on a tool approval. Wakes the Executor (`gate_resolved`), which
    resumes the same attempt. A domain form is answered by its own route."""
    return await attempts_repo.answer_attempt(db, run_id, attempt_id, data)


@router.post(f"{_ATTEMPT}/resume", response_model=AttemptOut)
async def attempt_resume(
    run_id: UUID, attempt_id: UUID, _: UUID = Depends(owned_run), db: AsyncSession = Depends(get_db)
):
    """Resume: the same attempt continues."""
    return await attempts_repo.resume_attempt(db, run_id, attempt_id)


@router.put(f"{_ATTEMPT}/pending-tools", response_model=AttemptOut)
async def attempt_pending_tools(
    run_id: UUID, attempt_id: UUID, data: attempt_schemas.PendingTools,
    path: Optional[WritePath] = Depends(declared_write_path),
    _: UUID = Depends(owned_run), db: AsyncSession = Depends(get_db),
):
    """The calls the attempt is running (attempt path): written before a call
    runs and when it ends, read by the retry rule (f) after a restart."""
    require_attempt_header(path, attempt_id)
    return await attempts_repo.set_pending_tools(db, run_id, attempt_id, data)


# ----------------------------------------------------------------------
# The agent scratchpad (attempt path: the request declares its attempt)
# ----------------------------------------------------------------------
@router.post("/{run_id}/scratchpad/register", response_model=NodeRunOut)
async def scratchpad_register(
    run_id: UUID,
    data: ScratchpadRegister,
    db: AsyncSession = Depends(fenced_db),
    _: UUID = Depends(owned_run),
):
    return await runs_repo.scratchpad_register(db, run_id, data)


@router.post("/{run_id}/scratchpad/notes", response_model=NodeRunOut)
async def scratchpad_note(
    run_id: UUID,
    data: ScratchpadNote,
    db: AsyncSession = Depends(fenced_db),
    _: UUID = Depends(owned_run),
):
    return await runs_repo.scratchpad_write_note(db, run_id, data)


@router.post("/{run_id}/scratchpad/flags", response_model=NodeRunOut)
async def scratchpad_flag(
    run_id: UUID,
    data: ScratchpadFlag,
    db: AsyncSession = Depends(fenced_db),
    _: UUID = Depends(owned_run),
):
    return await runs_repo.scratchpad_raise_flag(db, run_id, data)


@router.post("/{run_id}/scratchpad/status", response_model=NodeRunOut)
async def scratchpad_status(
    run_id: UUID,
    data: ScratchpadSetStatus,
    db: AsyncSession = Depends(fenced_db),
    _: UUID = Depends(owned_run),
):
    return await runs_repo.scratchpad_set_status(db, run_id, data)


@router.post("/{run_id}/scratchpad/summary", response_model=NodeRunOut)
async def scratchpad_summary(
    run_id: UUID,
    data: ScratchpadSummary,
    db: AsyncSession = Depends(fenced_db),
    _: UUID = Depends(owned_run),
):
    return await runs_repo.scratchpad_write_summary(db, run_id, data)


@router.put("/{run_id}/output", response_model=NodeRunOut)
async def write_output(
    run_id: UUID,
    data: AgentOutputWrite,
    db: AsyncSession = Depends(fenced_db),
    _: UUID = Depends(owned_run),
):
    """`write_agent_output`. Replaces this run's own document; never another's."""
    return await runs_repo.write_agent_output(db, run_id, data)
