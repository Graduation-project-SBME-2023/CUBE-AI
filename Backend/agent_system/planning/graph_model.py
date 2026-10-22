"""The session graph as the planning side sees it (merged plan, Phase 8.1).

Pure Pydantic, no I/O:

- ``NodeDef`` and ``ChangeContent`` are exactly ``graph_changes.content``
  (Appendix P), the same shape as ``application_api/core/graph_content.py``;
  a contract test holds the two together. The Application API computes the
  digest; nothing here does.
- ``GraphState`` is the live sequence the Diff Engine reads, each node with a
  summary of its current run.
- ``GraphDiff`` is what the Main, the Composer and the Recovery Agent propose:
  a list of ops on the sequence. There is no ``Rebind`` and no ``AddAfter``:
  the order *is* the sequence (D5), and inputs follow D23.
- ``apply_ops(state, diff)`` turns a diff into the full target content.

Node ids are UUIDs minted by whoever proposes the node (D7); there are no
local refs. ``ValidationError`` here is the graph error (code, node, detail),
not pydantic's.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CONTENT_VERSION = 1
ExpectationOp = Literal["==", "!=", "<", "<=", ">", ">="]
NodeKind = Literal["agent", "tool"]
OnRunning = Literal["reject", "restart"]
RunStatus = Literal["running", "completed", "failed", "cancelled"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InputSource(_Model):
    node_id: UUID
    port: str = Field(min_length=1)


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------
class ValidationError(_Model):
    """One technical error in a graph change (system §9). It names what is
    wrong and never suggests a strategy."""

    code: str
    node: Optional[UUID] = None
    detail: str = ""
    # AMBIGUOUS_INPUT: every source the node could name in input_sources.
    candidates: list[InputSource] = Field(default_factory=list)


class DiffError(ValueError):
    """``apply_ops`` could not build the target sequence."""

    def __init__(self, errors: list[ValidationError]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{e.code}: {e.detail}" for e in errors))


# ----------------------------------------------------------------------
# graph_changes.content (v1)
# ----------------------------------------------------------------------
class InputSources(_Model):
    """``{v, ports: {port: {node_id, port}}}``; only for ports with several candidates."""

    v: Literal[1] = 1
    ports: dict[str, InputSource]


class Expectation(_Model):
    metric: str = Field(min_length=1)
    op: ExpectationOp
    value: Any
    # The output port whose summary holds the metric; any output port when omitted.
    port: Optional[str] = None


class Expectations(_Model):
    v: Literal[1] = 1
    rules: list[Expectation]


class SeedFrom(_Model):
    """Start a rerun from a message of an earlier run of the same node: the
    engine forks that run's SDK session up to the message (D27)."""

    run_id: UUID
    message_uuid: str = Field(min_length=1, max_length=256)


class NodeDef(_Model):
    node_id: UUID
    kind: NodeKind
    ref: str = Field(min_length=1, max_length=255)
    ref_version: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=255)
    params: dict[str, Any] = Field(default_factory=dict)
    input_sources: Optional[InputSources] = None
    expect: Optional[Expectations] = None
    # D28, part of the definition: `before` waits for the user's release,
    # `after` holds the next node for the user's review.
    approval: Literal["none", "before", "after"] = "none"
    # Only on a node this change reruns; not part of the stored definition.
    seed_from: Optional[SeedFrom] = None

    def definition(self) -> dict[str, Any]:
        """What identifies the node's work: a difference is an edit (affected set)."""
        return self.model_dump(mode="json", exclude_none=True, exclude={"node_id", "seed_from"})


class ChangeContent(_Model):
    """``graph_changes.content``: the full target sequence, in order."""

    v: Literal[1] = CONTENT_VERSION
    nodes: list[NodeDef]
    rerun_node_ids: list[UUID] = Field(default_factory=list)
    release_hold_node_ids: list[UUID] = Field(default_factory=list)
    on_running: OnRunning = "reject"
    origin: Optional[str] = Field(default=None, max_length=64)

    def document(self) -> dict[str, Any]:
        """The JSON the Application API stores and digests."""
        return self.model_dump(mode="json", exclude_none=True)

    def node(self, node_id: UUID) -> Optional[NodeDef]:
        return next((node for node in self.nodes if node.node_id == node_id), None)


# ----------------------------------------------------------------------
# The live graph
# ----------------------------------------------------------------------
class RunSummary(_Model):
    """A node's current run, as far as planning needs it."""

    run_id: UUID
    status: RunStatus
    # An expectation hold that no change released yet.
    holds_downstream: bool = False


class NodeState(_Model):
    definition: NodeDef
    node_seq: Optional[int] = None
    run: Optional[RunSummary] = None
    # Every run of the node known to the reader (the current one included);
    # BAD_SEED checks a seed against it.
    run_ids: frozenset[UUID] = frozenset()

    @property
    def node_id(self) -> UUID:
        return self.definition.node_id

    @property
    def status(self) -> Literal["not_started", "running", "completed", "failed", "cancelled"]:
        return self.run.status if self.run else "not_started"


class GraphState(_Model):
    """The live sequence in ``position`` order, at ``version``."""

    version: int = Field(ge=0)
    nodes: list[NodeState] = Field(default_factory=list)
    # Ids of tombstoned nodes: a new node must not reuse one (I4).
    removed_node_ids: frozenset[UUID] = frozenset()

    def node(self, node_id: UUID) -> Optional[NodeState]:
        return next((node for node in self.nodes if node.node_id == node_id), None)

    @property
    def sequence(self) -> list[NodeDef]:
        return [node.definition for node in self.nodes]

    @property
    def used_node_ids(self) -> frozenset[UUID]:
        return frozenset(node.node_id for node in self.nodes) | self.removed_node_ids


# ----------------------------------------------------------------------
# GraphDiff
# ----------------------------------------------------------------------
class AddNode(_Model):
    """Insert a new node at index ``at`` of the sequence (the end when omitted)."""

    op: Literal["add_node"] = "add_node"
    node_id: UUID = Field(default_factory=uuid.uuid4)
    kind: NodeKind
    ref: str = Field(min_length=1, max_length=255)
    # The catalog version when omitted.
    ref_version: Optional[str] = Field(default=None, min_length=1)
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    params: dict[str, Any] = Field(default_factory=dict)
    input_sources: Optional[dict[str, InputSource]] = None
    expect: Optional[list[Expectation]] = None
    # D28, from the user's words: approve the node before it starts, or its result after.
    approval: Literal["none", "before", "after"] = "none"
    at: Optional[int] = Field(default=None, ge=0)


class RemoveNode(_Model):
    op: Literal["remove_node"] = "remove_node"
    node_id: UUID


class MoveNode(_Model):
    """Move a node to index ``to`` of the sequence without it."""

    op: Literal["move_node"] = "move_node"
    node_id: UUID
    to: int = Field(ge=0)


class UpdateParams(_Model):
    """Replace the node's params (and its title, when given)."""

    op: Literal["update_params"] = "update_params"
    node_id: UUID
    params: dict[str, Any]
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)


class SetInputSources(_Model):
    """Name the source of input ports; an empty mapping clears them."""

    op: Literal["set_input_sources"] = "set_input_sources"
    node_id: UUID
    ports: dict[str, InputSource]


class SetExpectations(_Model):
    """Replace the node's expectations; an empty list clears them."""

    op: Literal["set_expectations"] = "set_expectations"
    node_id: UUID
    rules: list[Expectation]


class ReleaseHold(_Model):
    op: Literal["release_hold"] = "release_hold"
    node_id: UUID


class RerunNode(_Model):
    """A new run of the node; ``seed_from`` (agent nodes only) starts it from
    a message of an earlier run of the same node. Still a new run (I8)."""

    op: Literal["rerun_node"] = "rerun_node"
    node_id: UUID
    seed_from: Optional[SeedFrom] = None


GraphOp = Annotated[
    Union[AddNode, RemoveNode, MoveNode, UpdateParams, SetInputSources, SetExpectations,
          ReleaseHold, RerunNode],
    Field(discriminator="op"),
]


class GraphDiff(_Model):
    base_version: int = Field(ge=0)
    ops: list[GraphOp] = Field(min_length=1)
    on_running: OnRunning = "reject"
    origin: Optional[Literal["composer", "recovery"]] = None


class GraphProposal(_Model):
    """A Composer draft. It stays in memory until the Main applies it (D19)."""

    proposal_id: UUID = Field(default_factory=uuid.uuid4)
    diff: GraphDiff
    rationale: str
    errors: list[ValidationError] = Field(default_factory=list)


class RecoveryProposal(_Model):
    """The Recovery Agent's answer (Plan 13)."""

    diagnosis: str
    diff: Optional[GraphDiff] = None
    user_message: str


# ----------------------------------------------------------------------
# apply_ops
# ----------------------------------------------------------------------
def _find(sequence: list[NodeDef], node_id: UUID) -> Optional[int]:
    return next((i for i, node in enumerate(sequence) if node.node_id == node_id), None)


def apply_ops(
    state: GraphState, diff: GraphDiff, *, versions: Mapping[str, str] | None = None
) -> ChangeContent:
    """The full target content of ``diff`` applied to ``state``, ops in order.

    ``versions`` maps a ref to its catalog version, for ``AddNode`` without
    one. Raises ``DiffError`` when an op names a node outside the sequence or
    an index past its end; every other rule is the Validator's.
    """
    versions = versions or {}
    sequence = list(state.sequence)
    reruns: dict[UUID, Optional[SeedFrom]] = {}
    releases: list[UUID] = []
    errors: list[ValidationError] = []

    def fail(code: str, node_id: UUID, detail: str) -> None:
        errors.append(ValidationError(code=code, node=node_id, detail=detail))

    for number, op in enumerate(diff.ops, start=1):
        where = f"op {number} ({op.op})"
        if isinstance(op, AddNode):
            if _find(sequence, op.node_id) is not None:
                fail("DUPLICATE_NODE", op.node_id, f"{where}: node {op.node_id} is already in the sequence")
                continue
            at = len(sequence) if op.at is None else op.at
            if at > len(sequence):
                fail("BAD_POSITION", op.node_id, f"{where}: index {at} is past the end ({len(sequence)})")
                continue
            sequence.insert(at, NodeDef(
                node_id=op.node_id, kind=op.kind, ref=op.ref,
                ref_version=op.ref_version or versions.get(op.ref) or "1",
                title=op.title or op.ref, params=op.params,
                input_sources=InputSources(ports=op.input_sources) if op.input_sources else None,
                expect=Expectations(rules=op.expect) if op.expect else None,
                approval=op.approval,
            ))
            continue

        index = _find(sequence, op.node_id)
        if index is None:
            fail("UNKNOWN_NODE", op.node_id, f"{where}: node {op.node_id} is not in the sequence")
            continue
        node = sequence[index]
        if isinstance(op, RemoveNode):
            del sequence[index]
            reruns.pop(op.node_id, None)
            releases = [node_id for node_id in releases if node_id != op.node_id]
        elif isinstance(op, MoveNode):
            del sequence[index]
            if op.to > len(sequence):
                fail("BAD_POSITION", op.node_id, f"{where}: index {op.to} is past the end ({len(sequence)})")
                sequence.insert(index, node)
            else:
                sequence.insert(op.to, node)
        elif isinstance(op, UpdateParams):
            sequence[index] = node.model_copy(update={"params": op.params, "title": op.title or node.title})
        elif isinstance(op, SetInputSources):
            sources = InputSources(ports=op.ports) if op.ports else None
            sequence[index] = node.model_copy(update={"input_sources": sources})
        elif isinstance(op, SetExpectations):
            expect = Expectations(rules=op.rules) if op.rules else None
            sequence[index] = node.model_copy(update={"expect": expect})
        elif isinstance(op, ReleaseHold):
            if op.node_id not in releases:
                releases.append(op.node_id)
        elif isinstance(op, RerunNode):
            reruns[op.node_id] = op.seed_from

    if errors:
        raise DiffError(errors)
    nodes = [
        node.model_copy(update={"seed_from": reruns.get(node.node_id)}) for node in sequence
    ]
    return ChangeContent(
        nodes=nodes,
        rerun_node_ids=list(reruns),
        release_hold_node_ids=releases,
        on_running=diff.on_running,
        origin=diff.origin,
    )
