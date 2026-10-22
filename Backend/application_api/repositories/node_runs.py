"""Node-run repository: the reads, the `node_status` view, starting a node
(first start or retry), and the agent scratchpad (attempt path)."""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from application_api.core.canonical_json import content_digest
from application_api.core.execution_rules import LatestAttempt, retry_verdict
from application_api.core.graph_content import NodeDef
from application_api.db.models.graph_changes import GraphChange
from application_api.db.models.graph_nodes import GraphNode
from application_api.db.models.node_runs import NodeRun
from application_api.db.models.run_attempts import QUESTION_KINDS, RunAttempt
from application_api.db.models.sessions import Session as SessionModel
from application_api.repositories import agent_transcript_entries as transcripts_repo
from application_api.repositories import run_attempts as attempts_repo
from application_api.schemas import node_runs as schemas
from application_api.services.fencing import HARNESS, fenced_transaction
from application_api.services.publication import conflict, database_refusal, lock_run, publication

logger = logging.getLogger(__name__)

SCRATCHPAD_SCHEMA_VERSION = schemas.SCRATCHPAD_SCHEMA_VERSION

# The UI speaks in upper-case node statuses. A live node with no current run
# has not started (or its last run was superseded): PENDING.
_UI_STATUS = {
    None: "PENDING",
    "running": "RUNNING",
    "completed": "COMPLETED",
    "failed": "FAILED",
    "cancelled": "CANCELLED",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# ----------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------
def _current_runs():
    """The current runs of live nodes, in node order."""
    return (
        select(NodeRun)
        .options(selectinload(NodeRun.node))
        .join(GraphNode, GraphNode.node_id == NodeRun.node_id)
        .where(NodeRun.superseded_at.is_(None), GraphNode.removed_at.is_(None))
        .order_by(GraphNode.position)
    )


async def list_runs(db: AsyncSession, session_id: UUID) -> list[NodeRun]:
    """The current run of every live node of the session, in node order."""
    result = await db.execute(_current_runs().where(NodeRun.session_id == session_id))
    return list(result.unique().scalars().all())


async def get_run(db: AsyncSession, run_id: UUID) -> NodeRun:
    run = (
        await db.execute(
            select(NodeRun).options(selectinload(NodeRun.node)).where(NodeRun.run_id == run_id)
        )
    ).unique().scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return run


async def list_node_runs(db: AsyncSession, node_id: UUID) -> list[NodeRun]:
    """A node's run history, oldest first, superseded runs included."""
    result = await db.execute(
        select(NodeRun)
        .options(selectinload(NodeRun.node))
        .where(NodeRun.node_id == node_id)
        .order_by(NodeRun.started_at.nulls_first(), NodeRun.run_id)
    )
    return list(result.unique().scalars().all())


async def resolve_run_for_target(
    db: AsyncSession, session_id: UUID, target_name: str
) -> tuple[NodeRun | None, int]:
    """The first current run, in node order, of a node with this target.
    Several nodes may legitimately name one target."""
    runs = [run for run in await list_runs(db, session_id) if run.node.target_name == target_name]
    return (runs[0] if runs else None), len(runs)


_NODE_STATUS = text(
    """
    SELECT n.node_id, n.node_seq, n.position, n.target_name, n.title,
           r.run_id, r.status, r.started_at, r.completed_at, r.error, r.downstream_hold,
           r.hold_released_by_change_id, a.state AS attempt_state, a.suspension ->> 'kind' AS suspension_kind,
           n.approval, n.start_released_by_change_id
    FROM graph_nodes n
    LEFT JOIN node_runs r ON r.node_id = n.node_id AND r.superseded_at IS NULL
    LEFT JOIN LATERAL (SELECT x.state, x.suspension FROM run_attempts x WHERE x.run_id = r.run_id
                       ORDER BY x.attempt_number DESC LIMIT 1) a ON true
    WHERE n.session_id = :sid AND n.removed_at IS NULL
    ORDER BY n.position
    """
)


async def node_status_payload(db: AsyncSession, session_id: UUID) -> dict[str, Any]:
    """The `node_status` payload: every live node with its current run."""
    rows = (await db.execute(_NODE_STATUS, {"sid": str(session_id)})).all()
    return {
        "schema_version": 2,
        "agents": [
            {
                "name": row.target_name,
                "title": row.title,
                "node_id": str(row.node_id),
                "node_seq": row.node_seq,
                "position": row.position,
                "run_id": str(row.run_id) if row.run_id else None,
                "status": _UI_STATUS[row.status],
                "attempt_state": row.attempt_state,
                # A question or a tool approval inside the node, a review hold
                # on its result, or an unreleased `approval: before` start (D28).
                "waiting_for_user": (
                    (row.attempt_state == "suspended" and row.suspension_kind in QUESTION_KINDS)
                    or (row.downstream_hold == "user_review" and not row.hold_released_by_change_id)
                    or (row.run_id is None and row.approval == "before"
                        and row.start_released_by_change_id is None)
                ),
                "approval": row.approval,
                "holds_downstream": bool(row.downstream_hold and not row.hold_released_by_change_id),
                "started_at": _iso(row.started_at),
                "finished_at": _iso(row.completed_at),
                "error": (row.error or {}).get("message") or (row.error or {}).get("code"),
            }
            for row in rows
        ],
    }


# ----------------------------------------------------------------------
# Start a node -- the first start creates the run with attempt 1
# ----------------------------------------------------------------------
_PREVIOUS_LIVE = text(
    """
    SELECT n.node_id, r.status, r.downstream_hold IS NOT NULL
               AND r.hold_released_by_change_id IS NULL AS holds
    FROM graph_nodes n
    LEFT JOIN node_runs r ON r.node_id = n.node_id AND r.superseded_at IS NULL
    WHERE n.session_id = :sid AND n.removed_at IS NULL AND n.position < :position
    ORDER BY n.position DESC LIMIT 1
    """
)
_ACTIVE_ATTEMPT = text(
    "SELECT attempt_id FROM run_attempts WHERE session_id = :sid "
    "AND run_attempt_state_is_active(state) LIMIT 1"
)
_UPSTREAM = text(
    """
    SELECT n.node_id, n.position, n.removed_at, r.run_id, r.status, r.outputs
    FROM graph_nodes n
    LEFT JOIN node_runs r ON r.node_id = n.node_id AND r.superseded_at IS NULL
    WHERE n.session_id = :sid AND n.node_id = ANY(:ids)
    """
)


def _not_ready(code: str, message: str, **extra: Any) -> HTTPException:
    return conflict(code, message, **extra)


async def _require_ready(db: AsyncSession, node: GraphNode, session: SessionModel, data: schemas.NodeStart) -> None:
    """The node and its graph, read under the session lock."""
    if node.removed_at is not None:
        raise _not_ready("NODE_REMOVED", f"Node {node.node_id} was removed; it cannot start.")
    if session.control_state != "active":
        raise _not_ready("SESSION_NOT_ACTIVE", f"Session {session.session_id} is {session.control_state}.")
    if node.accepted_by_change_id != data.accepted_by_change_id:
        raise _not_ready(
            "STALE_DEFINITION",
            f"Node {node.node_id} is now accepted by change {node.accepted_by_change_id}; re-read the graph.",
            accepted_by_change_id=str(node.accepted_by_change_id),
        )
    previous = (
        await db.execute(_PREVIOUS_LIVE, {"sid": str(node.session_id), "position": node.position})
    ).one_or_none()
    if previous is not None and (previous.status != "completed" or previous.holds):
        raise _not_ready(
            "NOT_READY",
            f"The previous live node {previous.node_id} has "
            + ("a hold on its result" if previous.holds else f"no completed run ({previous.status or 'not started'})")
            + ".",
        )
    if node.approval == "before" and node.start_released_by_change_id is None:
        raise _not_ready(
            "NEEDS_RELEASE",
            f"Node {node.node_id} waits for the user to release it (approval: before).",
        )
    active = (await db.execute(_ACTIVE_ATTEMPT, {"sid": str(node.session_id)})).scalar()
    if active is not None:
        raise _not_ready(
            "ATTEMPT_ACTIVE",
            f"Attempt {active} of this session is not terminal; one runs at a time.",
            attempt_id=str(active),
        )


async def _resolve_pins(
    db: AsyncSession, node: GraphNode, inputs: schemas.ResolvedInputs
) -> dict[str, Any]:
    """At resolution time, each pin is the current completed run of the
    selected live upstream node. `type` and `ref` come from that run's outputs."""
    ids = list({pin.node_id for pin in inputs.ports.values()})
    rows = {
        row.node_id: row
        for row in (await db.execute(_UPSTREAM, {"sid": str(node.session_id), "ids": ids})).all()
    } if ids else {}
    sources = (node.input_sources or {}).get("ports", {})
    ports: dict[str, Any] = {}
    for port, pin in inputs.ports.items():
        upstream = rows.get(pin.node_id)

        def refuse(reason: str) -> HTTPException:
            return conflict("BAD_PIN", f"Input {port!r}: {reason}", status_code=409, port=port)

        if upstream is None or upstream.removed_at is not None or upstream.position >= node.position:
            raise refuse(f"node {pin.node_id} is not a live node earlier in the sequence")
        source = sources.get(port)
        if source is not None and (source["node_id"], source["port"]) != (str(pin.node_id), pin.port):
            raise refuse(f"the node names {source['node_id']}:{source['port']} as its source (input_sources)")
        if upstream.run_id != pin.run_id or upstream.status != "completed":
            raise refuse(f"run {pin.run_id} is not the current completed run of node {pin.node_id}")
        produced = (upstream.outputs or {}).get("ports", {}).get(pin.port)
        if produced is None:
            raise refuse(f"run {pin.run_id} has no output port {pin.port!r}")
        if pin.type not in (None, produced["type"]) or pin.ref not in (None, produced["ref"]):
            raise refuse(f"the pin does not match output {pin.port!r} of run {pin.run_id}")
        ports[port] = {
            "node_id": str(pin.node_id),
            "run_id": str(pin.run_id),
            "port": pin.port,
            "type": produced["type"],
            "ref": produced["ref"],
        }
    extras = inputs.model_dump(mode="json", exclude={"v", "ports"})
    return {"v": 1, "ports": ports, "target_version": node.target_version, **extras}


async def _accepted_definition(db: AsyncSession, node: GraphNode) -> tuple[Optional[NodeDef], bool]:
    """The node's definition in its accepting change, and whether that change
    reran it (`execution_policy.fresh`)."""
    content = (
        await db.execute(select(GraphChange.content).where(GraphChange.change_id == node.accepted_by_change_id))
    ).scalar_one()
    reran = str(node.node_id) in {str(node_id) for node_id in content.get("rerun_node_ids", [])}
    for entry in content.get("nodes", []):
        if str(entry.get("node_id")) == str(node.node_id) and "seed_from" in entry:
            return NodeDef.model_validate(entry), reran
    return None, reran


async def _seed_verdict(
    db: AsyncSession, node: GraphNode, seed, privacy_mode: str, data: schemas.NodeStart
) -> dict[str, Any]:
    """T4 step 5 (D27): the seed applies only when its transcript still holds
    the message, it is a run of this node, and its privacy mode, spec digest
    and engine version match. Nothing is copied: the engine forks the source
    session up to the message, reading it as this run's first attempt."""
    frozen = {"run_id": str(seed.run_id), "message_uuid": seed.message_uuid, "applied": False}
    if node.target_kind != "agent" or not data.limits.resumable:
        return {**frozen, "reason": "not a resumable agent"}
    source = (
        await db.execute(select(NodeRun.node_id, NodeRun.execution_policy).where(NodeRun.run_id == seed.run_id))
    ).one_or_none()
    if source is None or not await transcripts_repo.has_message(db, seed.run_id, seed.message_uuid):
        return {**frozen, "reason": "transcript or message not found (purged?)"}
    if source.node_id != node.node_id:
        return {**frozen, "reason": "transcript of another node"}
    for key in ("spec_digest", "engine_version"):
        if source.execution_policy.get(key) != getattr(data, key):
            return {**frozen, "reason": f"{key} differs"}
    if source.execution_policy.get("privacy_mode") != privacy_mode:
        return {**frozen, "reason": "privacy_mode differs"}
    return {**frozen, "applied": True}


def _submission_key(run_id: UUID, tool: str, request: Optional[dict[str, Any]], number: int) -> str:
    """`{sha256(run_id|tool|args)}.{attempt_number}`: also the Celery task id."""
    return f"{content_digest([str(run_id), tool, request or {}])}.{number}"


def _attempt(run: NodeRun, number: int, data: schemas.NodeStart, tool: str,
             request: Optional[dict[str, Any]]) -> RunAttempt:
    return RunAttempt(
        run_id=run.run_id,
        session_id=run.session_id,
        attempt_number=number,
        dispatch_kind=data.dispatch_kind,
        state="preparing",
        request=request,
        submission_key=_submission_key(run.run_id, tool, request, number) if data.dispatch_kind == "job" else None,
    )


async def start_node(
    db: AsyncSession, data: schemas.NodeStart
) -> tuple[NodeRun, RunAttempt, bool]:
    """Start a node. Returns the run, its new attempt (state `preparing`) and whether
    this was the run's first start."""
    session_id = (
        await db.execute(select(GraphNode.session_id).where(GraphNode.node_id == data.node_id))
    ).scalar()
    if session_id is None:
        raise HTTPException(status_code=404, detail=f"Node {data.node_id} not found")

    async with fenced_transaction(db, HARNESS), publication(db, session_id) as publish:
        node = (
            await db.execute(
                select(GraphNode).where(GraphNode.node_id == data.node_id)
                .with_for_update().execution_options(populate_existing=True)
            )
        ).scalar_one()
        session = (
            await db.execute(
                select(SessionModel).where(SessionModel.session_id == session_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        await _require_ready(db, node, session, data)
        current = (
            await db.execute(
                select(NodeRun).where(NodeRun.node_id == node.node_id, NodeRun.superseded_at.is_(None))
                .with_for_update().execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

        if current is None:
            run, attempt = await _first_start(db, node, session, data)
        else:
            run, attempt = await _retry(db, node, current, data)
        publish(
            "run_attempt_started",
            {
                "node_id": str(node.node_id),
                "run_id": str(run.run_id),
                "attempt_id": str(attempt.attempt_id),
                "attempt_number": attempt.attempt_number,
                "first_start": current is None,
                "seed_from": run.execution_policy.get("seed_from"),
            },
        )
        await attempts_repo.publish_progress(db, session_id, publish)
    # Read back with the node, for the response.
    return await get_run(db, run.run_id), attempt, current is None


async def _first_start(
    db: AsyncSession, node: GraphNode, session: SessionModel, data: schemas.NodeStart
) -> tuple[NodeRun, RunAttempt]:
    is_agent = node.target_kind == "agent"
    if is_agent != (data.compiled_prompt is not None):
        raise conflict(
            "BAD_PROMPT",
            "An agent node starts with its compiled prompt; a tool node without one.",
            status_code=422,
        )
    if data.resolved_inputs is None:
        raise conflict("INPUTS_REQUIRED", "A first start pins its inputs (resolved_inputs).", status_code=422)
    resolved = await _resolve_pins(db, node, data.resolved_inputs)
    definition, fresh = await _accepted_definition(db, node)
    policy: dict[str, Any] = {
        "v": 1,
        **data.limits.model_dump(mode="json", exclude_none=True),
        "fresh": fresh,
        "privacy_mode": session.privacy_mode,
        **{key: value for key in ("spec_digest", "engine_version") if (value := getattr(data, key)) is not None},
    }
    if definition is not None and definition.seed_from is not None:
        policy["seed_from"] = await _seed_verdict(db, node, definition.seed_from, session.privacy_mode, data)
    run = NodeRun(
        node_id=node.node_id,
        session_id=node.session_id,
        accepted_by_change_id=node.accepted_by_change_id,
        execution_policy=policy,
        target_version=node.target_version,
        compiled_prompt=data.compiled_prompt,
        resolved_inputs=resolved,
        status="running",
        started_at=_now(),
    )
    db.add(run)
    await db.flush()
    attempt = _attempt(run, 1, data, node.target_name, data.request)
    db.add(attempt)
    await db.flush()
    return run, attempt


async def _retry(
    db: AsyncSession, node: GraphNode, run: NodeRun, data: schemas.NodeStart
) -> tuple[NodeRun, RunAttempt]:
    latest = (
        await db.execute(
            select(RunAttempt).where(RunAttempt.run_id == run.run_id)
            .order_by(RunAttempt.attempt_number.desc()).limit(1)
        )
    ).scalar_one()
    verdict = retry_verdict(
        run_status=run.status,
        latest=LatestAttempt(
            latest.attempt_number, latest.state, latest.dispatched_at is not None,
            (latest.error or {}).get("code"),
        ),
        policy=run.execution_policy,
        explicit=data.explicit_retry,
        pending_tools=(latest.pending_tools or {}).get("calls"),
    )
    if not verdict.allowed:
        raise conflict(verdict.code, f"Run {run.run_id} cannot be retried: {verdict.reason}.")
    attempt = _attempt(run, latest.attempt_number + 1, data, node.target_name,
                       data.request if data.request is not None else latest.request)
    db.add(attempt)
    run.status, run.completed_at, run.error = "running", None, None
    await db.flush()
    return run, attempt


# ----------------------------------------------------------------------
# Scratchpad and agent output
# ----------------------------------------------------------------------
def _empty_notes(role: str) -> dict[str, Any]:
    return {
        "schema_version": SCRATCHPAD_SCHEMA_VERSION,
        "role": role,
        "status": "in_progress",
        "registered_at": _iso(_now()),
        "notes": [],
        "activity_log": [],
    }


def _log_activity(notes: dict[str, Any], action: str, detail: str) -> None:
    notes.setdefault("activity_log", []).append(
        {"action": action, "detail": detail, "timestamp": _iso(_now())}
    )


async def _mutate(db: AsyncSession, run_id: UUID, change) -> NodeRun:
    """Serialise scratchpad/output writes on the run row, then commit. The
    request declared its attempt, so only the run's running attempt
    writes here; the database refuses any other."""
    await lock_run(db, run_id)
    run = await get_run(db, run_id)
    await db.refresh(run)
    if run.status == "completed":
        raise await _abort(db, HTTPException(status_code=409, detail=f"Run {run_id} is completed and immutable."))
    try:
        result = change(run)
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except IntegrityError as exc:
        await db.rollback()
        raise database_refusal(exc) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    await db.refresh(run)
    return result if result is not None else run


async def _abort(db: AsyncSession, exc: HTTPException) -> HTTPException:
    await db.rollback()
    return exc


async def scratchpad_register(
    db: AsyncSession, run_id: UUID, data: schemas.ScratchpadRegister
) -> NodeRun:
    def change(run: NodeRun):
        if run.scratchpad_notes and run.scratchpad_notes.get("registered_at"):
            raise HTTPException(
                status_code=409,
                detail=f"Run {run_id} is already registered.",
            )
        notes = _empty_notes(data.role)
        _log_activity(notes, "registered", f"Role: {data.role}")
        run.scratchpad_notes = notes

    return await _mutate(db, run_id, change)


def _require_registered(run: NodeRun) -> dict[str, Any]:
    notes = copy.deepcopy(run.scratchpad_notes or {})
    if not notes.get("registered_at"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Run {run.run_id} is not registered. Call "
                "scratchpad_register_agent first."
            ),
        )
    return notes


async def scratchpad_write_note(
    db: AsyncSession, run_id: UUID, data: schemas.ScratchpadNote
) -> NodeRun:
    def change(run: NodeRun):
        notes = _require_registered(run)
        notes.setdefault("notes", []).append(
            {"content": data.content, "created_at": _iso(_now())}
        )
        _log_activity(notes, "wrote_note", f"Note #{len(notes['notes']) - 1}")
        run.scratchpad_notes = notes

    return await _mutate(db, run_id, change)


async def scratchpad_raise_flag(
    db: AsyncSession, run_id: UUID, data: schemas.ScratchpadFlag
) -> NodeRun:
    def change(run: NodeRun):
        notes = _require_registered(run)
        warnings = copy.deepcopy(run.scratchpad_warnings or {"schema_version": 1, "items": []})
        warnings.setdefault("items", []).append(
            {
                "level": data.level,
                "message": data.message,
                "created_at": _iso(_now()),
                "source": "scratchpad_flag",
            }
        )
        _log_activity(
            notes, "raised_flag", f"[{data.level.upper()}] {data.message[:120]}"
        )
        run.scratchpad_warnings = warnings
        run.scratchpad_notes = notes

    return await _mutate(db, run_id, change)


async def scratchpad_set_status(
    db: AsyncSession, run_id: UUID, data: schemas.ScratchpadSetStatus
) -> NodeRun:
    def change(run: NodeRun):
        notes = _require_registered(run)
        previous = notes.get("status")
        notes["status"] = data.status
        _log_activity(notes, "status_change", f"{previous} -> {data.status}")
        run.scratchpad_notes = notes

    return await _mutate(db, run_id, change)


async def scratchpad_write_summary(
    db: AsyncSession, run_id: UUID, data: schemas.ScratchpadSummary
) -> NodeRun:
    def change(run: NodeRun):
        notes = _require_registered(run)
        summary = copy.deepcopy(run.scratchpad_summary or {"schema_version": SCRATCHPAD_SCHEMA_VERSION})
        if summary.get("scratchpad_summary") and not data.overwrite:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A summary already exists for this run. Set overwrite=true to "
                    "replace it."
                ),
            )
        summary["schema_version"] = SCRATCHPAD_SCHEMA_VERSION
        summary["scratchpad_summary"] = {
            "content": data.summary,
            "updated_at": _iso(_now()),
        }
        _log_activity(notes, "wrote_summary", f"{len(data.summary)} chars")
        run.scratchpad_summary = summary
        run.scratchpad_notes = notes

    return await _mutate(db, run_id, change)


async def write_agent_output(
    db: AsyncSession, run_id: UUID, data: schemas.AgentOutputWrite
) -> NodeRun:
    """`write_agent_output` — the structured result, no longer a JSON file."""

    def change(run: NodeRun):
        summary = copy.deepcopy(run.scratchpad_summary or {"schema_version": SCRATCHPAD_SCHEMA_VERSION})
        summary["schema_version"] = SCRATCHPAD_SCHEMA_VERSION
        summary["agent_output"] = {
            "agent_role": data.agent_role,
            "status": data.status,
            "recorded_at": _iso(_now()),
            "inputs": data.inputs,
            "outputs": {
                "files": data.output_files,
                "metrics": data.output_metrics,
                "summary": data.summary,
            },
            "errors": data.errors,
        }
        run.scratchpad_summary = summary

        if data.warnings:
            warnings = copy.deepcopy(run.scratchpad_warnings or {"schema_version": 1, "items": []})
            existing = {
                (item.get("message"), item.get("source"))
                for item in warnings.get("items", [])
            }
            for message in data.warnings:
                if (message, "agent_output") in existing:
                    continue
                warnings.setdefault("items", []).append(
                    {
                        "level": "warning",
                        "message": message,
                        "created_at": _iso(_now()),
                        "source": "agent_output",
                    }
                )
            run.scratchpad_warnings = warnings

    return await _mutate(db, run_id, change)


def scratchpad_report(runs: list[NodeRun]) -> dict[str, Any]:
    """The reporter's cross-agent view, assembled from the runs."""
    agents = []
    activity: list[dict[str, Any]] = []
    for run in runs:
        notes = run.scratchpad_notes or {}
        summary = (run.scratchpad_summary or {}).get("scratchpad_summary")
        flags = (run.scratchpad_warnings or {}).get("items", [])
        agents.append(
            {
                "agent_id": run.node.target_name,
                "run_id": str(run.run_id),
                "node_id": str(run.node_id),
                "position": run.node.position,
                "role": notes.get("role", ""),
                "status": notes.get("status") or _UI_STATUS[run.status].lower(),
                "run_status": run.status,
                "registered_at": notes.get("registered_at"),
                "flag_count": len(flags),
                "flags": flags,
                "has_summary": bool(summary),
                "summary": summary,
            }
        )
        for entry in notes.get("activity_log", []):
            activity.append({**entry, "agent_id": run.node.target_name, "role": notes.get("role", "")})

    statuses = {entry["status"] for entry in agents}
    if not agents:
        overall = "no_agents"
    elif "failed" in statuses:
        overall = "failed"
    elif "blocked" in statuses:
        overall = "blocked"
    elif statuses - {"done"}:
        overall = "in_progress"
    else:
        overall = "all_done"

    return {
        "overall_status": overall,
        "agent_count": len(agents),
        "agents": agents,
        "activity_log": activity,
    }
