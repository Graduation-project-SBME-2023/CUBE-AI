"""Run-attempt repository: dispatch, reconcile, complete and fail,
cancel, suspend / answer / resume (T10), and an attempt's pending tools.

Every transition locks session -> run -> attempt and runs on the
harness path, except the writes an attempt or a user makes itself: a worker
marking its own attempt running and an attempt setting its pending tools
(attempt path), and an answer (user path). The run's status is always written
in the same transaction as the attempt state it projects, which a deferred
trigger checks at commit.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.core.execution_rules import (
    PRODUCED_TABLES,
    REFERENCE_TABLES,
    SELF_TABLE,
    RunOutputs,
    unknown_output_tables,
    violated_expectations,
)
from application_api.db.models.graph_nodes import GraphNode
from application_api.db.models.node_runs import EXPECTATION_VIOLATED, USER_REVIEW, NodeRun
from application_api.db.models.run_attempts import (
    ACTIVE_ATTEMPT_STATES,
    QUESTION_KINDS,
    TERMINAL_ATTEMPT_STATES,
    TIMEOUT_CODES,
    RunAttempt,
)
from application_api.repositories import sessions as sessions_repo
from application_api.schemas import run_attempts as schemas
from application_api.services.fencing import HARNESS, USER, WritePath, fenced_transaction
from application_api.services.publication import conflict, database_refusal, lock_run, publication

logger = logging.getLogger(__name__)

Publish = Callable[[str, Any], Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ----------------------------------------------------------------------
# One locked transition
# ----------------------------------------------------------------------
@dataclass(slots=True)
class Transition:
    session_id: UUID
    run: NodeRun
    attempt: RunAttempt
    publish: Publish


async def _session_of(db: AsyncSession, run_id: UUID) -> UUID:
    session_id = (
        await db.execute(select(NodeRun.session_id).where(NodeRun.run_id == run_id))
    ).scalar()
    if session_id is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return session_id


async def lock_run_and_attempt(
    db: AsyncSession, run_id: UUID, attempt_id: UUID
) -> tuple[NodeRun, RunAttempt]:
    """session -> run -> attempt, then the rows as they are under the locks."""
    await lock_run(db, run_id)
    attempt = (
        await db.execute(
            select(RunAttempt)
            .where(RunAttempt.attempt_id == attempt_id, RunAttempt.run_id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if attempt is None:
        raise HTTPException(status_code=404, detail=f"Attempt {attempt_id} of run {run_id} not found")
    run = (
        await db.execute(
            select(NodeRun).where(NodeRun.run_id == run_id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    return run, attempt


@asynccontextmanager
async def transition(
    db: AsyncSession, run_id: UUID, attempt_id: UUID, path: WritePath = HARNESS
) -> AsyncIterator[Transition]:
    session_id = await _session_of(db, run_id)
    async with fenced_transaction(db, path), publication(db, session_id) as publish:
        run, attempt = await lock_run_and_attempt(db, run_id, attempt_id)
        yield Transition(session_id, run, attempt, publish)


async def _latest_number(db: AsyncSession, run_id: UUID) -> int:
    return (
        await db.execute(
            text("SELECT max(attempt_number) FROM run_attempts WHERE run_id = :rid"),
            {"rid": str(run_id)},
        )
    ).scalar_one()


async def _require_current(db: AsyncSession, t: Transition) -> None:
    """Only the run's latest attempt acts for it."""
    if t.attempt.attempt_number != await _latest_number(db, t.run.run_id):
        raise conflict(
            "STALE_ATTEMPT",
            f"Attempt {t.attempt.attempt_id} is not the latest attempt of run {t.run.run_id}.",
        )


def _require_active(t: Transition, *, allow_suspended: bool = True) -> None:
    state = t.attempt.state
    if state in TERMINAL_ATTEMPT_STATES:
        raise conflict("ATTEMPT_ENDED", f"Attempt {t.attempt.attempt_id} is {state} and final.")
    if state == "suspended" and not allow_suspended:
        raise conflict(
            "ATTEMPT_SUSPENDED",
            f"Attempt {t.attempt.attempt_id} is suspended; it is resumed first.",
        )


# ----------------------------------------------------------------------
# The run projection (node_runs.status) and the events
# ----------------------------------------------------------------------
def _project(run: NodeRun, attempt: RunAttempt) -> None:
    if attempt.state in ACTIVE_ATTEMPT_STATES:
        run.status, run.completed_at, run.error = "running", None, None
    elif attempt.state == "succeeded":
        run.status, run.completed_at, run.error = "completed", attempt.ended_at, None
    else:
        run.status = "cancelled" if attempt.state == "cancelled" else "failed"
        run.completed_at, run.error = attempt.ended_at, attempt.error


async def _end(
    db: AsyncSession,
    t: Transition,
    state: str,
    *,
    error: Optional[dict[str, Any]] = None,
    usage: Optional[schemas.Usage] = None,
) -> None:
    """Move the attempt to a terminal state, project it onto the run and add
    its usage to the session budget, all in this transaction."""
    attempt = t.attempt
    if state != "succeeded":
        error = error or attempt.error or {"code": "cancelled" if state == "cancelled" else state}
    attempt.state, attempt.ended_at, attempt.error = state, _now(), error
    if usage is not None:
        attempt.usage = usage.model_dump(mode="json")
    _project(t.run, attempt)
    await db.flush()
    if usage is not None:
        await sessions_repo.add_tokens(db, t.session_id, usage.total)
    t.publish(
        "run_attempt_ended",
        {
            "node_id": str(t.run.node_id),
            "run_id": str(t.run.run_id),
            "attempt_id": str(attempt.attempt_id),
            "attempt_number": attempt.attempt_number,
            "state": state,
            "run_status": t.run.status,
            "superseded": t.run.superseded_at is not None,
            "error": error,
        },
    )


async def publish_progress(db: AsyncSession, session_id: UUID, publish: Publish) -> None:
    """`node_status` for the UI and `session_state` for the inbox, as of now."""
    # Imported here: node_runs imports this module for the transitions.
    from application_api.repositories import node_runs as runs_repo

    publish("node_status", await runs_repo.node_status_payload(db, session_id))
    await sessions_repo.publish_session_state(db, session_id, publish)


# ----------------------------------------------------------------------
# Dispatch
# ----------------------------------------------------------------------
async def mark_dispatched(
    db: AsyncSession, run_id: UUID, attempt_id: UUID, data: schemas.AttemptDispatched
) -> RunAttempt:
    """In-process and inline: `running` before the spawn. Job: `submitted`
    after the send. A replay of an already recorded dispatch is a no-op."""
    async with transition(db, run_id, attempt_id) as t:
        attempt = t.attempt
        if attempt.state != "preparing":
            if attempt.dispatched_at is not None and attempt.state in ACTIVE_ATTEMPT_STATES:
                return attempt
            raise conflict("NOT_PREPARING", f"Attempt {attempt_id} is {attempt.state}.")
        if attempt.cancel_requested_at is not None and data.state == "running":
            # Not spawned yet, so nothing starts. A job that was already sent
            # is still recorded as submitted: its work may be running.
            raise conflict(
                "CANCEL_REQUESTED",
                f"Attempt {attempt_id} has a cancel request; it is not started.",
            )
        now = _now()
        attempt.state, attempt.dispatched_at = data.state, now
        if data.state == "running":
            attempt.started_at = now
        await publish_progress(db, t.session_id, t.publish)
    return attempt


async def mark_running(db: AsyncSession, run_id: UUID, attempt_id: UUID) -> RunAttempt:
    """A job worker moves its own attempt to running before it writes anything
    (attempt path: the worker is the attempt)."""
    async with transition(db, run_id, attempt_id, WritePath.attempt(attempt_id)) as t:
        attempt = t.attempt
        if attempt.state == "running":
            return attempt
        if attempt.state not in ("preparing", "submitted"):
            raise conflict("NOT_STARTABLE", f"Attempt {attempt_id} is {attempt.state}.")
        if attempt.cancel_requested_at is not None:
            raise conflict("CANCEL_REQUESTED", f"Attempt {attempt_id} has a cancel request.")
        now = _now()
        attempt.state = "running"
        attempt.dispatched_at = attempt.dispatched_at or now
        attempt.started_at = attempt.started_at or now
        await publish_progress(db, t.session_id, t.publish)
    return attempt


# ----------------------------------------------------------------------
# Complete / fail
# ----------------------------------------------------------------------
async def _check_outputs(db: AsyncSession, run_id: UUID, outputs: RunOutputs) -> None:
    """Every ref names rows this run produced, rows that exist, or the run
    itself. A render file is never an output."""
    if unknown := unknown_output_tables(outputs):
        raise conflict("INVALID_OUTPUTS", f"Outputs may not reference {unknown}.", status_code=422)
    for port, spec in outputs.ports.items():
        table, ids = spec.ref.table, sorted(set(spec.ref.ids))
        if table == SELF_TABLE:
            valid = ids == [str(run_id)]
        elif table in PRODUCED_TABLES:
            retained = " AND retention_class = 'retained'" if table == "stored_files" else ""
            found = (
                await db.execute(
                    text(
                        f"SELECT count(*) FROM {table} WHERE {PRODUCED_TABLES[table]}::text = ANY(:ids) "
                        f"AND created_by_run_id = :rid{retained}"
                    ),
                    {"ids": ids, "rid": str(run_id)},
                )
            ).scalar_one()
            valid = found == len(ids)
        else:
            found = (
                await db.execute(
                    text(f"SELECT count(*) FROM {table} WHERE {REFERENCE_TABLES[table]}::text = ANY(:ids)"),
                    {"ids": ids},
                )
            ).scalar_one()
            valid = found == len(ids)
        if not valid:
            raise conflict(
                "INVALID_OUTPUTS",
                f"Port {port!r} references {table} rows that do not exist or that run {run_id} "
                "did not produce.",
                status_code=422,
            )


async def _succeed(
    db: AsyncSession, t: Transition, outputs: RunOutputs, usage: Optional[schemas.Usage]
) -> None:
    await _check_outputs(db, t.run.run_id, outputs)
    document = outputs.model_dump(mode="json", exclude_none=True)
    t.run.outputs = document
    await _end(db, t, "succeeded", usage=usage)
    if t.run.superseded_at is not None:
        return  # a late success after supersession is history, never current
    node = (
        await db.execute(select(GraphNode).where(GraphNode.node_id == t.run.node_id))
    ).scalar_one()
    failures = violated_expectations(node.expectations, document)
    if failures:
        t.run.downstream_hold = EXPECTATION_VIOLATED
        t.publish(
            "expectation_violated",
            {"node_id": str(node.node_id), "run_id": str(t.run.run_id), "violations": failures},
        )
    elif node.approval == "after":
        # D28: the user asked to review this node's result before the next one starts.
        t.run.downstream_hold = USER_REVIEW
        t.publish(
            "approval_requested",
            {"node_id": str(node.node_id), "run_id": str(t.run.run_id), "kind": USER_REVIEW},
        )
    follows = (
        await db.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM graph_nodes WHERE session_id = :sid "
                "AND removed_at IS NULL AND position > :position)"
            ),
            {"sid": str(t.session_id), "position": node.position},
        )
    ).scalar_one()
    if not follows:
        t.publish("graph_idle", {"node_id": str(node.node_id), "run_id": str(t.run.run_id)})


async def complete_attempt(
    db: AsyncSession, run_id: UUID, attempt_id: UUID, data: schemas.AttemptComplete
) -> RunAttempt:
    async with transition(db, run_id, attempt_id) as t:
        _require_active(t, allow_suspended=False)
        await _require_current(db, t)
        await _succeed(db, t, data.outputs, data.usage)
        await publish_progress(db, t.session_id, t.publish)
    return t.attempt


async def fail_attempt(
    db: AsyncSession, run_id: UUID, attempt_id: UUID, data: schemas.AttemptFail
) -> RunAttempt:
    async with transition(db, run_id, attempt_id) as t:
        _require_active(t, allow_suspended=False)
        await _require_current(db, t)
        await _end(db, t, "failed", error=data.error.model_dump(mode="json"), usage=data.usage)
        await publish_progress(db, t.session_id, t.publish)
    return t.attempt


# ----------------------------------------------------------------------
# Cancel / timeout / privacy switch
# ----------------------------------------------------------------------
async def _confirm_stopped(db: AsyncSession, t: Transition, usage: Optional[schemas.Usage] = None) -> None:
    error = t.attempt.error or {}
    state = "failed" if error.get("code") in TIMEOUT_CODES else "cancelled"
    await _end(db, t, state, usage=usage)


async def request_cancel_locked(
    db: AsyncSession, t: Transition, reason: str, detail: Optional[str] = None
) -> bool:
    """Record a cancel request on a locked attempt; True when it is new. A
    suspended attempt holds no work, so its stop is confirmed at once."""
    attempt = t.attempt
    if attempt.state in TERMINAL_ATTEMPT_STATES or attempt.cancel_requested_at is not None:
        return False
    attempt.cancel_requested_at = _now()
    if reason in TIMEOUT_CODES:
        attempt.error = {"code": reason, "detail": detail}
    elif reason != "user":
        attempt.error = {"code": "cancelled", "detail": detail or reason}
    t.publish(
        "run_attempt_cancel_requested",
        {
            "node_id": str(t.run.node_id),
            "run_id": str(t.run.run_id),
            "attempt_id": str(attempt.attempt_id),
            "reason": reason,
        },
    )
    if attempt.state == "suspended":
        await _confirm_stopped(db, t)
    return True


async def request_cancel(
    db: AsyncSession, run_id: UUID, attempt_id: UUID, data: schemas.AttemptCancel
) -> RunAttempt:
    async with transition(db, run_id, attempt_id) as t:
        _require_active(t)
        if await request_cancel_locked(db, t, data.reason, data.detail):
            await publish_progress(db, t.session_id, t.publish)
    return t.attempt


async def confirm_stopped(
    db: AsyncSession, run_id: UUID, attempt_id: UUID, data: schemas.AttemptStopped
) -> RunAttempt:
    """Only once the task or job is observed stopped; never from a timeout alone."""
    async with transition(db, run_id, attempt_id) as t:
        if t.attempt.state in TERMINAL_ATTEMPT_STATES:
            return t.attempt  # a replayed confirmation
        if t.attempt.cancel_requested_at is None:
            raise conflict(
                "NO_CANCEL_REQUEST",
                f"Attempt {attempt_id} has no cancel request; an unrequested stop is reconciled.",
            )
        await _confirm_stopped(db, t, data.usage)
        await publish_progress(db, t.session_id, t.publish)
    return t.attempt


async def request_cancel_where_policy(
    db: AsyncSession, session_id: UUID, publish: Publish, *, privacy_mode: str, reason: str
) -> list[str]:
    """Inside the caller's transaction (session already locked): request the
    cancellation of every non-terminal attempt whose run's frozen policy has
    this privacy mode."""
    rows = (
        await db.execute(
            text(
                "SELECT a.run_id, a.attempt_id FROM run_attempts a JOIN node_runs r USING (run_id) "
                "WHERE a.session_id = :sid AND run_attempt_state_is_active(a.state) "
                "AND r.execution_policy ->> 'privacy_mode' = :mode ORDER BY a.run_id"
            ),
            {"sid": str(session_id), "mode": privacy_mode},
        )
    ).all()
    requested = []
    async with fenced_transaction(db, HARNESS):
        for run_id, attempt_id in rows:
            run, attempt = await lock_run_and_attempt(db, run_id, attempt_id)
            if await request_cancel_locked(db, Transition(session_id, run, attempt, publish), reason):
                requested.append(str(attempt_id))
    return requested


# ----------------------------------------------------------------------
# Reconcile
# ----------------------------------------------------------------------
async def reconcile_attempt(
    db: AsyncSession, run_id: UUID, attempt_id: UUID, data: schemas.AttemptReconcile
) -> tuple[RunAttempt, str]:
    """After a restart or a lost acknowledgement. No attempt is created here."""
    async with transition(db, run_id, attempt_id) as t:
        attempt, observed = t.attempt, data.observed
        if attempt.state in TERMINAL_ATTEMPT_STATES or attempt.state == "suspended":
            # A suspended attempt runs nothing; its state is in its transcript.
            return attempt, "unchanged"
        if attempt.dispatch_kind in ("in_process", "inline"):
            # The process that ran it is gone.
            action = "recorded"
            await _end(db, t, "interrupted", error={"code": "interrupted", "detail": "process restarted"},
                       usage=data.usage)
        elif observed in ("queued", "running"):
            action = "reattach"
            target = "running" if observed == "running" else "submitted"
            if attempt.state != target and not (attempt.state == "running" and target == "submitted"):
                attempt.state = target
                attempt.dispatched_at = attempt.dispatched_at or _now()
                if target == "running":
                    attempt.started_at = attempt.started_at or _now()
        elif observed == "succeeded":
            if data.outputs is None:
                raise conflict("OUTPUTS_REQUIRED", "A succeeded job is recorded with its outputs.",
                               status_code=422)
            action = "recorded"
            if attempt.state != "running":
                attempt.state = "running"
                attempt.dispatched_at = attempt.dispatched_at or _now()
                await db.flush()
            await _succeed(db, t, data.outputs, data.usage)
        elif observed in ("failed", "cancelled"):
            action = "recorded"
            error = data.error.model_dump(mode="json") if data.error else None
            await _end(db, t, observed, error=error or {"code": "error" if observed == "failed" else "cancelled"},
                       usage=data.usage)
        elif observed == "not_found":
            if attempt.dispatched_at is None:
                # Never sent: resubmit the same key and request.
                return attempt, "resubmit"
            action = "recorded"
            await _end(db, t, "interrupted", error={"code": "record_lost", "detail": "the backend has no record"})
        else:  # unreachable: blocks every start in the session until a later reconcile
            action = "unchanged"
            if attempt.state != "unknown":
                attempt.state = "unknown"
                action = "recorded"
        await publish_progress(db, t.session_id, t.publish)
    return t.attempt, action


# ----------------------------------------------------------------------
# Suspend / answer / resume: one attempt waits and continues, never a new one
# ----------------------------------------------------------------------
async def suspend_attempt(
    db: AsyncSession, run_id: UUID, attempt_id: UUID, data: schemas.AttemptSuspend
) -> RunAttempt:
    """An `ask_user` question, a tool approval or a pause: the attempt waits with
    no running work; its state is in its transcript."""
    async with transition(db, run_id, attempt_id) as t:
        attempt, suspension = t.attempt, data.suspension
        current = attempt.suspension or {}
        if attempt.state == "suspended" and (current.get("kind"), current.get("tool_use_id")) == (
            suspension.kind, suspension.tool_use_id
        ):
            return attempt  # a replayed suspend
        if attempt.state != "running" or t.run.superseded_at is not None:
            raise conflict(
                "NOT_SUSPENDABLE",
                f"Attempt {attempt_id} is {attempt.state}"
                + (" on a superseded run" if t.run.superseded_at else "")
                + "; only a running attempt of a current run suspends.",
            )
        if attempt.cancel_requested_at is not None:
            # A suspended attempt is confirmed stopped only when the cancel
            # arrives after it suspended, so it would wait forever here.
            raise conflict(
                "CANCEL_REQUESTED",
                f"Attempt {attempt_id} has a cancel request; confirm its stop instead.",
            )
        attempt.state = "suspended"
        attempt.suspension = {
            "v": 2, **suspension.model_dump(mode="json", exclude_none=True), "since": _now().isoformat()
        }
        t.publish(
            "run_attempt_suspended",
            {
                "node_id": str(t.run.node_id),
                "run_id": str(run_id),
                "attempt_id": str(attempt_id),
                "kind": suspension.kind,
                "tool_use_id": suspension.tool_use_id,
                "form": suspension.form,
                "payload": suspension.payload,
            },
        )
        await publish_progress(db, t.session_id, t.publish)
    return attempt


def _answer(
    t: Transition, *, tool_use_id: Optional[str], form: Optional[str], answer: Any = None,
    decision: Optional[str] = None, comment: Optional[str] = None,
) -> None:
    """Record the answer on the suspension; `gate_resolved` wakes the Executor,
    not the Main."""
    attempt = t.attempt
    suspension = attempt.suspension or {}
    if (
        attempt.state != "suspended"
        or suspension.get("kind") not in QUESTION_KINDS
        or "resume" in suspension
        or (tool_use_id is not None and suspension.get("tool_use_id") != tool_use_id)
        or (form is not None and suspension.get("form") != form)
        or (suspension.get("kind") == "tool_approval" and decision is None)
    ):
        raise conflict(
            "NO_OPEN_GATE",
            f"Run {t.run.run_id} has no unanswered {form or 'question'} waiting for this answer.",
        )
    if t.run.status != "running" or t.run.superseded_at is not None:
        raise conflict("RUN_NOT_CURRENT", f"Run {t.run.run_id} is not running and current.")
    given = {"answer": answer} if decision is None else {"decision": decision}
    attempt.suspension = {
        **suspension, "resume": {**given, "comment": comment, "decided_at": _now().isoformat()},
    }
    t.publish(
        "gate_resolved",
        {
            "node_id": str(t.run.node_id),
            "run_id": str(t.run.run_id),
            "attempt_id": str(attempt.attempt_id),
            "kind": suspension.get("kind"),
            "tool_use_id": suspension.get("tool_use_id"),
            "form": suspension.get("form"),
            "decision": decision,
        },
    )


async def answer_attempt(
    db: AsyncSession, run_id: UUID, attempt_id: UUID, data: schemas.AttemptAnswer
) -> RunAttempt:
    """The user's answer to an `ask_user` question or decision on a tool
    approval (the user gate path). A domain form is answered by its own route,
    which writes the form's columns in the same transaction."""
    async with transition(db, run_id, attempt_id, USER) as t:
        if (t.attempt.suspension or {}).get("form") is not None:
            raise conflict(
                "FORM_ANSWER",
                f"Attempt {attempt_id} waits on the {t.attempt.suspension['form']} form; answer it there.",
            )
        _answer(t, tool_use_id=data.tool_use_id, form=None, answer=data.answer, decision=data.decision,
                comment=data.comment)
        await sessions_repo.publish_session_state(db, t.session_id, t.publish)
    return t.attempt


async def resolve_domain_gate(
    db: AsyncSession, run_id: UUID, form: str, publish: Publish, *, decision: str = "approve",
    comment: Optional[str] = None,
) -> None:
    """T10's answer step for a domain form of `ask_user` (the cohort approval,
    the segmentation review), inside the caller's user-gate transaction, so the
    form's columns and the attempt's `resume` commit together."""
    attempt_id = (
        await db.execute(
            text(
                "SELECT attempt_id FROM run_attempts WHERE run_id = :rid AND state = 'suspended' "
                "AND suspension ->> 'kind' = 'ask_user' AND suspension ->> 'form' = :form"
            ),
            {"rid": str(run_id), "form": form},
        )
    ).scalar()
    if attempt_id is None:
        raise conflict("NO_OPEN_GATE", f"Run {run_id} is not waiting on its {form} form.")
    run, attempt = await lock_run_and_attempt(db, run_id, attempt_id)
    _answer(Transition(run.session_id, run, attempt, publish), tool_use_id=None, form=form,
            decision=decision, comment=comment)


async def resume_attempt(db: AsyncSession, run_id: UUID, attempt_id: UUID) -> RunAttempt:
    async with transition(db, run_id, attempt_id) as t:
        attempt = t.attempt
        if attempt.state == "running":
            return attempt  # a replayed resume
        suspension = attempt.suspension or {}
        control_state = await sessions_repo.locked_control_state(db, t.session_id)
        if (
            attempt.state != "suspended"
            or control_state != "active"
            or t.run.superseded_at is not None
            or (suspension.get("kind") in QUESTION_KINDS and "resume" not in suspension)
        ):
            raise conflict(
                "NOT_RESUMABLE",
                f"Attempt {attempt_id} resumes only while suspended, in an active session "
                f"(it is {control_state}), on a current run, and once answered.",
            )
        attempt.state, attempt.resumed_at = "running", _now()
        t.publish(
            "run_attempt_resumed",
            {"node_id": str(t.run.node_id), "run_id": str(run_id), "attempt_id": str(attempt_id),
             "resume": suspension.get("resume")},
        )
        await publish_progress(db, t.session_id, t.publish)
    return attempt


# ----------------------------------------------------------------------
# Pending tools (attempt path): the calls a running attempt is making
# ----------------------------------------------------------------------
async def set_pending_tools(
    db: AsyncSession, run_id: UUID, attempt_id: UUID, data: schemas.PendingTools
) -> RunAttempt:
    """Written before a call runs and when it ends, so a restart knows whether
    a retry is safe (T4 rule f). Only the run's latest attempt, running or
    suspended, writes its own; the fencing trigger checks it."""
    async with fenced_transaction(db, WritePath.attempt(attempt_id)):
        try:
            updated = (
                await db.execute(
                    update(RunAttempt)
                    .where(RunAttempt.attempt_id == attempt_id, RunAttempt.run_id == run_id)
                    .values(pending_tools=data.model_dump(mode="json"))
                    .returning(RunAttempt)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if updated is None:
                raise HTTPException(status_code=404, detail=f"Attempt {attempt_id} of run {run_id} not found")
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise database_refusal(exc) from exc
    return updated
