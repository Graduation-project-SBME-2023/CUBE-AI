"""Graph-change repository: propose, decide + apply, and the change
history.

A submitted change is applied at once (`decided_by = auto`) when it has no
approval reason, and proposed otherwise (`pending`). The Application API
derives PLAN_MODE and supersedes_completed itself, whatever the caller sent,
with the same `plan_apply()` rule the apply uses.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.core.config import settings
from application_api.core.graph_content import (
    ApplyPlan,
    ChangeContent,
    LiveNode,
    node_columns,
    plan_apply,
)
from application_api.db.models.chat_messages import ChatMessage
from application_api.db.models.chats import Chat
from application_api.db.models.graph_changes import GraphChange
from application_api.db.models.graph_nodes import GraphNode
from application_api.db.models.node_runs import NodeRun
from application_api.db.models.session_graphs import SessionGraph
from application_api.db.models.sessions import Session as SessionModel
from application_api.repositories import run_attempts as attempts_repo
from application_api.schemas import graph_changes as schemas
from application_api.services.fencing import HARNESS, fenced_transaction
from application_api.services.publication import conflict, lock_session_and_graph, publication

logger = logging.getLogger(__name__)

Publish = Callable[[str, Any], Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def main_idempotency_key(turn_id: int, call_id: str) -> str:
    """The Main's key: sha256(turn_id || call_id), so a re-handled turn never
    submits the same work twice."""
    return hashlib.sha256(f"{turn_id}\x1f{call_id}".encode("utf-8")).hexdigest()


def _expired(change: GraphChange, now: datetime) -> bool:
    return change.created_at < now - timedelta(hours=settings.APPROVAL_TTL_H)


# ----------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------
async def get_change(db: AsyncSession, change_id: UUID) -> GraphChange:
    change = (
        await db.execute(select(GraphChange).where(GraphChange.change_id == change_id))
    ).scalar_one_or_none()
    if change is None:
        raise HTTPException(status_code=404, detail=f"Graph change {change_id} not found")
    return change


async def list_changes(db: AsyncSession, session_id: UUID, limit: int = 100) -> list[GraphChange]:
    result = await db.execute(
        select(GraphChange)
        .where(GraphChange.session_id == session_id)
        .order_by(GraphChange.created_at.desc(), GraphChange.change_id)
        .limit(limit)
    )
    return list(result.scalars().all())


async def pending_change(db: AsyncSession, session_id: UUID, *, lock: bool = False) -> Optional[GraphChange]:
    query = select(GraphChange).where(
        GraphChange.session_id == session_id, GraphChange.status == "pending"
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(query)).scalar_one_or_none()


_LIVE = text(
    """
    SELECT n.node_id, n.position, n.target_kind, n.target_name, n.target_version, n.title,
           n.params, n.input_sources, n.expectations, r.run_id, r.status,
           (r.downstream_hold IS NOT NULL AND r.hold_released_by_change_id IS NULL) AS holds
    FROM graph_nodes n
    LEFT JOIN node_runs r ON r.node_id = n.node_id AND r.superseded_at IS NULL
    WHERE n.session_id = :sid AND n.removed_at IS NULL
    ORDER BY n.position
    """
)


async def live_nodes(db: AsyncSession, session_id: UUID) -> list[LiveNode]:
    return [
        LiveNode(
            node_id=row.node_id, position=row.position, kind=row.target_kind, ref=row.target_name,
            ref_version=row.target_version, title=row.title, params=row.params,
            input_sources=row.input_sources, expectations=row.expectations,
            run_id=row.run_id, run_status=row.status, holds_downstream=bool(row.holds),
        )
        for row in (await db.execute(_LIVE, {"sid": str(session_id)})).all()
    ]


# ----------------------------------------------------------------------
# The shared check: the plan the apply would carry out, read under the graph lock
# ----------------------------------------------------------------------
@dataclass(slots=True)
class _Locked:
    session: SessionModel
    graph: SessionGraph
    live: list[LiveNode]


async def _lock(db: AsyncSession, session_id: UUID) -> _Locked:
    await lock_session_and_graph(db, session_id)
    session = (
        await db.execute(
            select(SessionModel).where(SessionModel.session_id == session_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    graph = (
        await db.execute(
            select(SessionGraph).where(SessionGraph.session_id == session_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return _Locked(session, graph, await live_nodes(db, session_id))


def _refuse_archived(locked: _Locked) -> None:
    if locked.session.control_state == "archived":
        raise conflict(
            "ARCHIVED", f"Session {locked.session.session_id} is archived; its graph no longer changes."
        )


async def _plan(db: AsyncSession, locked: _Locked, content: ChangeContent) -> ApplyPlan:
    ids = [node.node_id for node in content.nodes]
    used = set(
        (await db.execute(select(GraphNode.node_id).where(GraphNode.node_id.in_(ids)))).scalars()
    ) if ids else set()
    return plan_apply(
        locked.live, content, used_node_ids=used, execution_mode=locked.session.execution_mode
    )


def _invalid(plan: ApplyPlan) -> HTTPException:
    return conflict(
        "INVALID_CHANGE",
        "; ".join(error.message for error in plan.errors),
        status_code=422,
        errors=[
            {"code": error.code, "node_id": str(error.node_id) if error.node_id else None,
             "detail": error.message}
            for error in plan.errors
        ],
    )


def _defined_ids(content: ChangeContent, plan: ApplyPlan) -> list[UUID]:
    """The nodes this change adds, edits or reruns, in sequence order."""
    accepting = plan.accepting
    return [node.node_id for node in content.nodes if node.node_id in accepting]


# ----------------------------------------------------------------------
# Apply (steps 4-8), shared by Auto apply and a user's approval
# ----------------------------------------------------------------------
async def _apply(
    db: AsyncSession, locked: _Locked, change: GraphChange, content: ChangeContent,
    plan: ApplyPlan, publish: Publish,
) -> list[UUID]:
    session_id, now = locked.graph.session_id, _now()
    # Live positions are unique, but a reorder passes through collisions.
    await db.execute(text("SET CONSTRAINTS ex_graph_nodes_live_position DEFERRED"))
    rows = {
        node.node_id: node
        for node in (
            await db.execute(
                select(GraphNode).where(GraphNode.session_id == session_id, GraphNode.removed_at.is_(None))
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    next_seq = (
        await db.execute(select(func.coalesce(func.max(GraphNode.node_seq), 0)).where(GraphNode.session_id == session_id))
    ).scalar_one()
    added = {node.node_id for node in plan.added}
    accepting = plan.accepting
    for position, node in enumerate(content.nodes, start=1):
        columns = node_columns(node)
        if node.node_id in added:
            next_seq += 1
            db.add(GraphNode(
                node_id=node.node_id, session_id=session_id, node_seq=next_seq, position=position,
                target_kind=node.kind, target_name=node.ref, accepted_by_change_id=change.change_id,
                **columns,
            ))
            continue
        row = rows[node.node_id]
        row.position = position
        for name, value in columns.items():
            setattr(row, name, value)
        if node.node_id in accepting:
            row.accepted_by_change_id = change.change_id
    for node in plan.removed:
        rows[node.node_id].removed_at = now
    await db.flush()

    # Step 6: supersede the current runs of affected nodes. A running run also
    # gets a cancel request on its active attempt (confirmed at once when the
    # attempt is suspended).
    superseded: list[UUID] = []
    for node in plan.superseded:
        run = (
            await db.execute(
                select(NodeRun).where(NodeRun.run_id == node.run_id)
                .with_for_update().execution_options(populate_existing=True)
            )
        ).scalar_one()
        run.superseded_at = now
        await db.flush()
        superseded.append(run.run_id)
        active = (
            await db.execute(
                text("SELECT attempt_id FROM run_attempts WHERE run_id = :rid AND run_attempt_state_is_active(state)"),
                {"rid": str(run.run_id)},
            )
        ).scalar()
        if active is not None:
            locked_run, attempt = await attempts_repo.lock_run_and_attempt(db, run.run_id, active)
            await attempts_repo.request_cancel_locked(
                db, attempts_repo.Transition(session_id, locked_run, attempt, publish),
                "harness", f"superseded by graph change {change.change_id}",
            )
        publish("node_run_superseded", {
            "node_id": str(node.node_id), "run_id": str(run.run_id), "change_id": str(change.change_id),
            "status": run.status,
        })

    # Step 7: release the holds this change names.
    for node in plan.released:
        run = (
            await db.execute(select(NodeRun).where(NodeRun.run_id == node.run_id).with_for_update())
        ).scalar_one()
        run.hold_released_by_change_id = change.change_id

    # Step 8: the version, then every other pending change goes stale.
    locked.graph.version += 1
    locked.graph.updated_at = now
    await db.flush()
    others = (
        await db.execute(
            select(GraphChange).where(
                GraphChange.session_id == session_id, GraphChange.status == "pending",
                GraphChange.change_id != change.change_id,
            ).with_for_update()
        )
    ).scalars().all()
    for other in others:
        other.status = "stale"
        publish("graph_change_stale", {"change_id": str(other.change_id), "superseded_by": str(change.change_id)})
    publish("graph_change_applied", {
        "change_id": str(change.change_id),
        "version": locked.graph.version,
        "decided_by": change.decided_by,
        "node_ids": [str(node_id) for node_id in _defined_ids(content, plan)],
        "removed_node_ids": [str(node.node_id) for node in plan.removed],
        "affected": [str(node_id) for node_id in plan.affected],
        "superseded_run_ids": [str(run_id) for run_id in superseded],
    })
    return superseded


async def _tool_result(
    db: AsyncSession, session_id: UUID, call: schemas.ToolCallRef, result: dict[str, Any]
) -> None:
    """The Main's control-tool result, committed with the change. The
    pairing trigger refuses it unless the call was committed first."""
    chat_id = (await db.execute(select(Chat.chat_id).where(Chat.session_id == session_id))).scalar_one()
    db.add(ChatMessage(
        chat_id=chat_id,
        role="tool",
        text=call.tool,
        metadata_={
            "v": 2, "kind": "tool_result", "turn_id": call.turn_id, "call_id": call.call_id,
            "tool": call.tool, "status": "ok", "ended_at": _now().isoformat(), "result": result,
            "phi": False,
        },
    ))
    await db.flush()


def _result(change: GraphChange, version: int, plan: Optional[ApplyPlan], content: ChangeContent,
            superseded: list[UUID] = (), replayed: bool = False) -> schemas.GraphChangeResult:
    return schemas.GraphChangeResult(
        change=schemas.GraphChangeOut.model_validate(change),
        version=version,
        node_ids=_defined_ids(content, plan) if plan else [],
        affected=plan.affected if plan else [],
        superseded_run_ids=list(superseded),
        replayed=replayed,
    )


# ----------------------------------------------------------------------
# Submit
# ----------------------------------------------------------------------
async def submit_change(
    db: AsyncSession, session_id: UUID, data: schemas.GraphChangeSubmit, idempotency_key: Optional[str],
) -> schemas.GraphChangeResult:
    if data.author == "main":
        if data.tool_call is None:
            raise conflict("TOOL_CALL_REQUIRED", "A change from the Main names its tool call.", status_code=422)
        idempotency_key = main_idempotency_key(data.tool_call.turn_id, data.tool_call.call_id)
    elif not idempotency_key:
        raise conflict("IDEMPOTENCY_KEY_REQUIRED", "Send an Idempotency-Key header.", status_code=422)
    content = data.content
    document, digest = content.document(), content.digest()

    async with fenced_transaction(db, HARNESS), publication(db, session_id) as publish:
        locked = await _lock(db, session_id)
        existing = (
            await db.execute(
                select(GraphChange).where(
                    GraphChange.session_id == session_id, GraphChange.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.content_digest != digest:
                raise conflict(
                    "IDEMPOTENCY_CONFLICT",
                    f"Key {idempotency_key!r} already submitted change {existing.change_id} with other content.",
                    change_id=str(existing.change_id),
                )
            return _result(existing, locked.graph.version, None, content, replayed=True)
        _refuse_archived(locked)
        if data.base_version != locked.graph.version:
            raise conflict(
                "STALE_BASE",
                f"The graph is at version {locked.graph.version}, not {data.base_version}; re-read and re-propose.",
                version=locked.graph.version,
            )
        plan = await _plan(db, locked, content)
        if plan.errors:
            raise _invalid(plan)
        reasons = sorted(set(data.approval_reasons) | plan.reasons)
        now = _now()
        change = GraphChange(
            session_id=session_id, author=data.author, idempotency_key=idempotency_key,
            base_version=data.base_version, content=document, content_digest=digest,
            approval_reasons=reasons, created_at=now,
        )
        superseded: list[UUID] = []
        if reasons:
            # Propose: one pending change per session; the old one goes first.
            previous = await pending_change(db, session_id, lock=True)
            if previous is not None:
                if _expired(previous, now):
                    previous.status = "expired"
                    publish("graph_change_expired", {"change_id": str(previous.change_id)})
                else:
                    previous.status = "withdrawn"
                    publish("graph_change_withdrawn", {"change_id": str(previous.change_id)})
                await db.flush()
            change.status = "pending"
            db.add(change)
            await db.flush()
            publish("graph_change_proposed", {
                "change_id": str(change.change_id), "author": data.author,
                "base_version": data.base_version, "approval_reasons": reasons,
                "content_digest": digest, "affected": [str(node_id) for node_id in plan.affected],
            })
            publish("approval_requested", {"change_id": str(change.change_id), "approval_reasons": reasons})
        else:
            # Auto: inserted applied, nothing waits.
            change.status, change.decided_by, change.decided_at = "applied", "auto", now
            change.applied_version = data.base_version + 1
            db.add(change)
            await db.flush()
            superseded = await _apply(db, locked, change, content, plan, publish)
        if data.tool_call is not None:
            await _tool_result(db, session_id, data.tool_call, {
                "change_id": str(change.change_id),
                "status": change.status,
                "node_ids": [str(node_id) for node_id in _defined_ids(content, plan)],
            })
        await attempts_repo.publish_progress(db, session_id, publish)
    return _result(change, locked.graph.version, plan, content, superseded)


# ----------------------------------------------------------------------
# A user's decision on a pending change
# ----------------------------------------------------------------------
async def decide_change(
    db: AsyncSession, change_id: UUID, data: schemas.GraphChangeDecision
) -> schemas.GraphChangeResult:
    change = await get_change(db, change_id)
    session_id = change.session_id
    refusal: Optional[HTTPException] = None
    plan: Optional[ApplyPlan] = None
    superseded: list[UUID] = []
    async with fenced_transaction(db, HARNESS), publication(db, session_id) as publish:
        locked = await _lock(db, session_id)
        _refuse_archived(locked)
        change = (
            await db.execute(
                select(GraphChange).where(GraphChange.change_id == change_id)
                .with_for_update().execution_options(populate_existing=True)
            )
        ).scalar_one()
        content = ChangeContent.model_validate(change.content)
        wanted = "applied" if data.decision == "approve" else "rejected"
        if change.status != "pending":
            if change.status == wanted and change.content_digest == data.content_digest:
                return _result(change, locked.graph.version, None, content, replayed=True)
            raise conflict("NOT_PENDING", f"Change {change_id} is {change.status}.", status=change.status)
        if data.content_digest != change.content_digest:
            raise conflict(
                "DIGEST_MISMATCH",
                "The decision quotes another content digest; a decision binds to exact content.",
            )
        now = _now()
        if _expired(change, now):
            change.status = "expired"
            publish("graph_change_expired", {"change_id": str(change_id)})
            refusal = conflict("EXPIRED", f"Change {change_id} is older than {settings.APPROVAL_TTL_H} h.")
        elif data.decision == "reject":
            change.status, change.decided_by, change.decided_at = "rejected", "user", now
            publish("graph_change_rejected", {"change_id": str(change_id), "comment": data.comment})
        else:
            stale = None
            if change.base_version != locked.graph.version:
                stale = f"the graph moved to version {locked.graph.version}"
            else:
                plan = await _plan(db, locked, content)
                missing = plan.reasons - set(change.approval_reasons)
                if plan.errors or missing:
                    stale = "; ".join(
                        [error.message for error in plan.errors]
                        + ([f"it now also needs {sorted(missing)}"] if missing else [])
                    )
            if stale:
                change.status = "stale"
                publish("graph_change_stale", {"change_id": str(change_id), "reason": stale})
                refusal = conflict("STALE_BASE", f"Change {change_id} is stale: {stale}. Re-propose it.")
            else:
                change.status, change.decided_by, change.decided_at = "applied", "user", now
                change.applied_version = change.base_version + 1
                await db.flush()
                superseded = await _apply(db, locked, change, content, plan, publish)
        await attempts_repo.publish_progress(db, session_id, publish)
    if refusal is not None:
        # The stale or expired status is committed; the decision itself is refused.
        raise refusal
    return _result(change, locked.graph.version, plan, content, superseded)
