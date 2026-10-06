"""Graph-change content and the pure part of proposing and applying a change.

``ChangeContent`` is exactly ``graph_changes.content``: the full target
sequence. ``plan_apply()`` compares it with the live graph and returns what
applying does -- inserts, edits, tombstones, the affected suffix, the runs it supersedes
-- and the approval reasons the Application API derives itself. It reads
no database, so proposing (to derive reasons) and applying share one rule, and
a diff engine can be tested against it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from application_api.core.canonical_json import content_digest

CONTENT_VERSION = 1
# The derivable approval reasons; every other reason comes from the manifests.
PLAN_MODE = "PLAN_MODE"
SUPERSEDES_COMPLETED = "supersedes_completed"
DERIVED_REASONS = frozenset({PLAN_MODE, SUPERSEDES_COMPLETED})
EXPECTATION_OPS = ("==", "!=", "<", "<=", ">", ">=")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InputSource(_Strict):
    node_id: UUID
    port: str = Field(min_length=1)


class InputSources(_Strict):
    """``{v, ports: {port: {node_id, port}}}``: the source of each input port
    that has several candidates. Stored as ``graph_nodes.input_sources``."""

    v: Literal[1] = 1
    ports: dict[str, InputSource]


class Expectation(_Strict):
    metric: str = Field(min_length=1)
    op: Literal["==", "!=", "<", "<=", ">", ">="]
    value: Any
    # The output port whose summary holds the metric; any port when omitted.
    port: Optional[str] = None


class Expectations(_Strict):
    """``{v, rules: [...]}``, checked at completion against the output summaries."""

    v: Literal[1] = 1
    rules: list[Expectation]


class SeedFrom(_Strict):
    """Start a rerun from a checkpoint of an earlier run of the same node."""

    run_id: UUID
    checkpoint_id: str = Field(min_length=1)
    checkpoint_ns: str = ""


class NodeDef(_Strict):
    """One node of the target sequence. New nodes carry the UUID
    the proposer minted, so the content names every node it defines."""

    node_id: UUID
    kind: Literal["agent", "tool"]
    ref: str = Field(min_length=1, max_length=255)
    ref_version: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=255)
    params: dict[str, Any] = Field(default_factory=dict)
    input_sources: Optional[InputSources] = None
    expect: Optional[Expectations] = None
    # Only on a node this change reruns (it is not part of the stored definition).
    seed_from: Optional[SeedFrom] = None


class ChangeContent(_Strict):
    """``graph_changes.content`` (v1)."""

    v: Literal[1] = CONTENT_VERSION
    nodes: list[NodeDef]
    rerun_node_ids: list[UUID] = Field(default_factory=list)
    release_hold_node_ids: list[UUID] = Field(default_factory=list)
    on_running: Literal["reject", "restart"] = "reject"
    origin: Optional[str] = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _references_are_closed(self) -> ChangeContent:
        ids = [node.node_id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("DUPLICATE_NODE: a node_id appears twice in the sequence")
        known = set(ids)
        for name in ("rerun_node_ids", "release_hold_node_ids"):
            listed = getattr(self, name)
            if len(listed) != len(set(listed)):
                raise ValueError(f"{name} lists a node twice")
            if unknown := set(listed) - known:
                raise ValueError(f"{name} names nodes outside the sequence: {sorted(map(str, unknown))}")
        reruns = set(self.rerun_node_ids)
        for node in self.nodes:
            if node.seed_from is not None and (node.kind != "agent" or node.node_id not in reruns):
                raise ValueError(f"BAD_SEED: node {node.node_id} sets seed_from but is not a rerun agent node")
        return self

    def document(self) -> dict[str, Any]:
        """The stored JSON: normalised (lower-case UUIDs, no null keys)."""
        return self.model_dump(mode="json", exclude_none=True)

    def digest(self) -> str:
        return content_digest(self.document())

    def node(self, node_id: UUID) -> NodeDef:
        return next(node for node in self.nodes if node.node_id == node_id)


# ----------------------------------------------------------------------
# The live graph, as proposing and applying read it under the lock
# ----------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class LiveNode:
    node_id: UUID
    position: int
    kind: str
    ref: str
    ref_version: str
    title: str
    params: dict[str, Any]
    input_sources: Optional[dict[str, Any]]
    expectations: Optional[dict[str, Any]]
    # The node's current run, when it has one.
    run_id: Optional[UUID] = None
    run_status: Optional[str] = None
    # An expectation hold on the current run that no change released yet.
    holds_downstream: bool = False

    def definition(self) -> tuple:
        return (self.title, self.params, self.ref_version, self.input_sources, self.expectations)


def node_columns(node: NodeDef) -> dict[str, Any]:
    """The editable `graph_nodes` columns a node definition stores."""
    dump = node.model_dump(mode="json", exclude_none=True)
    return {
        "title": dump["title"],
        "params": dump["params"],
        "target_version": dump["ref_version"],
        "input_sources": dump.get("input_sources"),
        "expectations": dump.get("expect"),
    }


def _definition(node: NodeDef) -> tuple:
    columns = node_columns(node)
    return (
        columns["title"],
        columns["params"],
        columns["target_version"],
        columns["input_sources"],
        columns["expectations"],
    )


@dataclass(frozen=True, slots=True)
class GraphError:
    code: str
    message: str
    node_id: Optional[UUID] = None


@dataclass(slots=True)
class ApplyPlan:
    """What applying does, in sequence order; empty `errors` means it may apply."""

    reruns: set[UUID] = field(default_factory=set)
    added: list[NodeDef] = field(default_factory=list)
    edited: list[NodeDef] = field(default_factory=list)
    retained: list[NodeDef] = field(default_factory=list)
    removed: list[LiveNode] = field(default_factory=list)
    affected: list[UUID] = field(default_factory=list)
    # Current runs that applying supersedes (affected nodes that have one).
    superseded: list[LiveNode] = field(default_factory=list)
    released: list[LiveNode] = field(default_factory=list)
    reasons: set[str] = field(default_factory=set)
    errors: list[GraphError] = field(default_factory=list)

    @property
    def accepting(self) -> set[UUID]:
        """Nodes whose accepted_by_change_id becomes this change."""
        return {node.node_id for node in (*self.added, *self.edited)} | self.reruns


def plan_apply(
    live: list[LiveNode],
    content: ChangeContent,
    *,
    used_node_ids: set[UUID],
    execution_mode: str,
) -> ApplyPlan:
    """The apply rule over a sequential graph.

    ``live`` is the live sequence in position order; ``used_node_ids`` is every
    node id that exists anywhere (a new node must mint an unused one).
    """
    plan = ApplyPlan(reruns=set(content.rerun_node_ids))
    by_id = {node.node_id: node for node in live}
    target = content.nodes

    for node in target:
        current = by_id.get(node.node_id)
        if current is None:
            if node.node_id in used_node_ids:
                plan.errors.append(GraphError(
                    "DUPLICATE_NODE",
                    f"node {node.node_id} already exists or was removed; a new node needs a new id",
                    node.node_id,
                ))
            plan.added.append(node)
            continue
        if (node.kind, node.ref) != (current.kind, current.ref):
            plan.errors.append(GraphError(
                "TARGET_IMMUTABLE",
                f"node {node.node_id} runs {current.kind} {current.ref!r}; a different target is a new node",
                node.node_id,
            ))
        (plan.edited if _definition(node) != current.definition() else plan.retained).append(node)

    kept = {node.node_id for node in target}
    plan.removed = [node for node in live if node.node_id not in kept]

    # An input_sources entry names a live node earlier in the new sequence.
    order = {node.node_id: index for index, node in enumerate(target)}
    for index, node in enumerate(target):
        for port, source in (node.input_sources.ports.items() if node.input_sources else ()):
            if order.get(source.node_id, index) >= index:
                plan.errors.append(GraphError(
                    "BAD_INPUT_SOURCE",
                    f"node {node.node_id} port {port!r} names {source.node_id}, which is not a live "
                    "node earlier in the sequence",
                    node.node_id,
                ))

    # Affected = the changed, moved, rerun or removed nodes, plus every live
    # node after the first of them (the sequential suffix).
    old_order = [node.node_id for node in live]
    new_order = [node.node_id for node in target]
    touched = {node.node_id for node in (*plan.added, *plan.edited)} | plan.reruns
    first = next(
        (i for i in range(len(new_order)) if new_order[i] in touched
         or i >= len(old_order) or old_order[i] != new_order[i]),
        len(new_order),
    )
    plan.affected = new_order[first:] + [node.node_id for node in plan.removed]
    for node_id in plan.affected:
        current = by_id.get(node_id)
        if current is None or current.run_id is None:
            continue
        plan.superseded.append(current)
        if current.run_status == "running" and content.on_running == "reject":
            plan.errors.append(GraphError(
                "RUNNING_NODE_AFFECTED",
                f"node {node_id} is running and the change affects it; resend with on_running=restart",
                node_id,
            ))

    for node_id in content.release_hold_node_ids:
        current = by_id.get(node_id)
        if current is None or not current.holds_downstream:
            plan.errors.append(GraphError(
                "BAD_RELEASE", f"node {node_id} holds nothing downstream to release", node_id
            ))
        elif node_id not in plan.affected:
            plan.released.append(current)

    if any(node.run_status == "completed" for node in plan.superseded):
        plan.reasons.add(SUPERSEDES_COMPLETED)
    # PLAN_MODE covers adding, editing, rerunning, reordering and releasing
    # work; removing nodes and cancelling need no approval.
    retained_old = [node_id for node_id in old_order if node_id in kept]
    retained_new = [node_id for node_id in new_order if node_id in by_id]
    touches_work = bool(
        plan.added or plan.edited or plan.reruns or content.release_hold_node_ids
        or retained_old != retained_new
    )
    if execution_mode == "plan" and touches_work:
        plan.reasons.add(PLAN_MODE)
    return plan
