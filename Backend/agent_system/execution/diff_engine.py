"""The Diff Engine (merged plan, Phase 8.4; system §26, §27).

``plan_change(state, diff)`` is pure: it turns a ``GraphDiff`` into the full
target content, validates it, and says what applying would do to each
affected node:

| Node state | Unaffected | Affected |
|---|---|---|
| not started | stays | its definition is updated; nothing to cancel |
| running | continues | ``on_running="restart"``: superseded and its attempt cancelled; otherwise ``RUNNING_NODE_AFFECTED`` |
| completed / failed / cancelled | its current run stays (the reuse) | superseded, never deleted; a completed run adds ``supersedes_completed`` |

``apply()`` submits the content through ``GraphStore.submit_change``: the
Application API applies it at once (T2) when no approval reason applies and
proposes it (T1) otherwise. Superseding, cancel requests and the durable events
are the Application API's, in the same transaction. On an applied change the
engine wakes the Executor (``graph_changed``).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field
from typing import Literal, Optional
from uuid import UUID

from ..planning.graph_model import (
    ChangeContent,
    DiffError,
    Expectations,
    GraphDiff,
    GraphState,
    InputSources,
    NodeDef,
    NodeState,
    RunSummary,
    ValidationError,
    apply_ops,
)
from ..planning.validator import validate
from ..toolkit.catalog import Catalog
from .affected import affected
from .graph_store import ChangeResult, GraphStore, GraphStoreError, GraphView, Run

logger = logging.getLogger(__name__)

SUPERSEDES_COMPLETED = "supersedes_completed"
type Author = Literal["main", "user", "harness"]
# Called with the session id after a change is applied: the Executor's graph_changed.
type GraphChanged = Callable[[str], Awaitable[None] | None]


def main_idempotency_key(turn_id: int, call_id: str) -> str:
    """sha256(turn_id || call_id): a re-handled Main turn never submits twice.
    The same key the Application API derives from ``tool_call``."""
    return hashlib.sha256(f"{turn_id}\x1f{call_id}".encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Supersession:
    """A current run that applying the change supersedes."""

    node_id: UUID
    run_id: UUID
    status: str
    # A running run also gets a cancel request on its attempt.
    cancel: bool
    supersedes_completed: bool


@dataclass(slots=True)
class ChangeOutcome:
    content: Optional[ChangeContent]
    affected: list[UUID] = field(default_factory=list)
    runs_to_supersede: list[Supersession] = field(default_factory=list)
    errors: list[ValidationError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def approval_reasons(self) -> tuple[str, ...]:
        """The reason the database also derives from the affected set."""
        if any(item.supersedes_completed for item in self.runs_to_supersede):
            return (SUPERSEDES_COMPLETED,)
        return ()


@dataclass(slots=True)
class ApplyResult:
    outcome: ChangeOutcome
    change: Optional[ChangeResult] = None
    errors: list[ValidationError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.change is not None and not self.errors

    @property
    def status(self) -> Optional[str]:
        return self.change.change.status if self.change else None


# ----------------------------------------------------------------------
# GraphView -> GraphState
# ----------------------------------------------------------------------
def state_from_view(view: GraphView, histories: Optional[dict[str, Sequence[Run]]] = None) -> GraphState:
    """The planning view of a graph read from the store. ``histories`` adds
    the earlier runs of some nodes (for ``seed_from``)."""
    histories = histories or {}
    nodes = []
    for item in view.nodes:
        node, run = item.node, item.run
        definition = NodeDef(
            node_id=UUID(node.node_id), kind=node.target_kind, ref=node.target_name,
            ref_version=node.target_version, title=node.title, params=node.params,
            input_sources=InputSources.model_validate(node.input_sources) if node.input_sources else None,
            expect=Expectations.model_validate(node.expectations) if node.expectations else None,
        )
        summary = None
        if run is not None:
            summary = RunSummary(
                run_id=UUID(run.run_id), status=run.status,
                holds_downstream=bool(run.downstream_hold and not run.hold_released_by_change_id),
            )
        run_ids = {UUID(r.run_id) for r in histories.get(node.node_id, ())}
        if run is not None:
            run_ids.add(UUID(run.run_id))
        nodes.append(NodeState(definition=definition, node_seq=node.node_seq, run=summary,
                               run_ids=frozenset(run_ids)))
    return GraphState(version=view.version, nodes=nodes)


# ----------------------------------------------------------------------
# The engine
# ----------------------------------------------------------------------
class DiffEngine:
    def __init__(
        self,
        store: GraphStore,
        catalog: Catalog,
        *,
        resource_pools: Optional[Collection[str]] = None,
        on_applied: Optional[GraphChanged] = None,
    ) -> None:
        self.store = store
        self.catalog = catalog
        self.resource_pools = resource_pools
        self.on_applied = on_applied

    # --- pure

    def _versions(self, diff: GraphDiff) -> dict[str, str]:
        refs = {op.ref for op in diff.ops if op.op == "add_node"}
        versions = {}
        for ref in refs:
            try:
                versions[ref] = str(self.catalog.resolve(ref).version)
            except KeyError:
                pass  # UNKNOWN_REF follows from the Validator
        return versions

    def plan_change(self, state: GraphState, diff: GraphDiff) -> ChangeOutcome:
        """The target content, the affected set and the runs it supersedes (§27)."""
        if diff.base_version != state.version:
            return ChangeOutcome(content=None, errors=[ValidationError(
                code="STALE_BASE", detail=f"the diff is based on version {diff.base_version}; "
                f"the graph is at version {state.version}",
            )])
        try:
            content = apply_ops(state, diff, versions=self._versions(diff))
        except DiffError as exc:
            return ChangeOutcome(content=None, errors=exc.errors)
        return self.plan_content(state, content)

    def plan_content(self, state: GraphState, content: ChangeContent) -> ChangeOutcome:
        outcome = ChangeOutcome(content=content, affected=affected(state.sequence, content))
        outcome.errors = validate(state, content, self.catalog, resource_pools=self.resource_pools)
        for node_id in outcome.affected:
            live = state.node(node_id)
            if live is None or live.run is None:
                continue  # never started: its definition is updated, nothing to cancel
            outcome.runs_to_supersede.append(Supersession(
                node_id=node_id, run_id=live.run.run_id, status=live.run.status,
                cancel=live.run.status == "running",
                supersedes_completed=live.run.status == "completed",
            ))
        return outcome

    # --- through the store

    async def load_state(self, session_id: str, *, history_for: Collection[UUID] = ()) -> GraphState:
        view = await self.store.get_graph(session_id)
        live = {item.node.node_id for item in view.nodes}
        histories = {
            str(node_id): await self.store.node_runs(str(node_id))
            for node_id in history_for if str(node_id) in live
        }
        return state_from_view(view, histories)

    async def apply(
        self,
        session_id: str,
        diff: GraphDiff,
        *,
        author: Author,
        turn_id: Optional[int] = None,
        call_id: Optional[str] = None,
        tool: str = "graph_apply",
        approval_reasons: Sequence[str] = (),
        idempotency_key: Optional[str] = None,
    ) -> ApplyResult:
        """Validate ``diff`` and submit it. ``approval_reasons`` are the ones
        the guardrails compute from the manifests (Plan 11); the database adds
        ``PLAN_MODE`` and ``supersedes_completed`` itself."""
        tool_call = None
        if author == "main":
            if turn_id is None or not call_id:
                raise ValueError("a change from the Main needs its turn_id and call_id")
            tool_call = {"turn_id": turn_id, "call_id": call_id, "tool": tool}
            idempotency_key = main_idempotency_key(turn_id, call_id)
        elif not idempotency_key:
            raise ValueError(f"a change from {author!r} needs an idempotency key")

        seeded = [op.node_id for op in diff.ops if op.op == "rerun_node" and op.seed_from is not None]
        state = await self.load_state(session_id, history_for=seeded)
        outcome = self.plan_change(state, diff)
        if not outcome.ok:
            return ApplyResult(outcome=outcome, errors=outcome.errors)

        reasons = tuple(sorted(set(approval_reasons) | set(outcome.approval_reasons)))
        try:
            change = await self.store.submit_change(
                session_id, author=author, base_version=diff.base_version,
                content=outcome.content.document(), approval_reasons=reasons,
                tool_call=tool_call, idempotency_key=idempotency_key,
            )
        except GraphStoreError as exc:
            if exc.status_code >= 500 or exc.code not in _CHANGE_REFUSALS:
                raise
            return ApplyResult(outcome=outcome, errors=[ValidationError(code=exc.code, detail=exc.message)])
        if change.change.status == "applied" and not change.replayed:
            await self._changed(session_id)
        return ApplyResult(outcome=outcome, change=change)

    async def decide(self, change_id: str, decision: Literal["approve", "reject"], content_digest: str,
                     *, session_id: str) -> ChangeResult:
        """A user's decision on a pending change, quoting its digest (Plan 11)."""
        result = await self.store.decide_change(change_id, decision, content_digest)
        if result.change.status == "applied" and not result.replayed:
            await self._changed(session_id)
        return result

    async def _changed(self, session_id: str) -> None:
        if self.on_applied is None:
            return
        woken = self.on_applied(session_id)
        if woken is not None:
            await woken


# The refusals of T1/T2 that answer the proposer (anything else is a fault).
_CHANGE_REFUSALS = frozenset({
    "STALE_BASE", "INVALID_CHANGE", "ARCHIVED", "IDEMPOTENCY_CONFLICT",
})

