"""GraphStore: the Executor's typed view of the session graph.

``GraphStore`` is the protocol the Executor programs against. ``HttpGraphStore``
is the real one, over the Application API (which owns every invariant).
``InMemoryGraphStore`` (``in_memory_graph_store.py``) is the test double and is
never looser than the database: the same scenario table runs against both.

Refusals are ``GraphStoreError`` with the Application API's stable codes
(STALE_BASE, NOT_READY, ATTEMPT_ACTIVE, MAX_ATTEMPTS, ...).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..clients.harness_client import ApplicationApiError, HarnessClient


class GraphStoreError(Exception):
    def __init__(self, code: str, message: str = "", status_code: int = 409) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status_code = status_code


# ----------------------------------------------------------------------
# Typed records (the Application API's JSON, minus what the Executor ignores)
# ----------------------------------------------------------------------
class _Record(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Attempt(_Record):
    attempt_id: str
    run_id: str
    attempt_number: int
    dispatch_kind: str
    state: str
    submission_key: Optional[str] = None
    request: Optional[dict[str, Any]] = None
    suspension: Optional[dict[str, Any]] = None
    dispatched_at: Optional[datetime] = None
    cancel_requested_at: Optional[datetime] = None
    resumed_at: Optional[datetime] = None
    error: Optional[dict[str, Any]] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class Run(_Record):
    run_id: str
    node_id: str
    status: str
    accepted_by_change_id: str
    execution_policy: dict[str, Any]
    target_version: str
    superseded_at: Optional[datetime] = None
    resolved_inputs: Optional[dict[str, Any]] = None
    outputs: Optional[dict[str, Any]] = None
    downstream_hold: Optional[str] = None
    hold_released_by_change_id: Optional[str] = None
    error: Optional[dict[str, Any]] = None
    attempts: list[Attempt] = Field(default_factory=list)


class Node(_Record):
    node_id: str
    node_seq: int
    position: int
    target_kind: str
    target_name: str
    target_version: str
    title: str
    params: dict[str, Any] = Field(default_factory=dict)
    input_sources: Optional[dict[str, Any]] = None
    expectations: Optional[dict[str, Any]] = None
    accepted_by_change_id: str


class LiveNode(_Record):
    node: Node
    run: Optional[Run] = None
    attempt: Optional[Attempt] = None


class Change(_Record):
    change_id: str
    status: str
    author: str
    base_version: int
    content: dict[str, Any]
    content_digest: str
    approval_reasons: list[str] = Field(default_factory=list)
    decided_by: Optional[str] = None
    applied_version: Optional[int] = None


class GraphView(_Record):
    session_id: str
    version: int
    nodes: list[LiveNode] = Field(default_factory=list)
    pending_change: Optional[Change] = None
    activity: str


class ChangeResult(_Record):
    change: Change
    version: int
    node_ids: list[str] = Field(default_factory=list)
    affected: list[str] = Field(default_factory=list)
    superseded_run_ids: list[str] = Field(default_factory=list)
    replayed: bool = False


class StartResult(_Record):
    run: Run
    attempt: Attempt
    first_start: bool


class StartRequest(_Record):
    """Start a node. `limits` are the manifest's (max_attempts, timeout_s, idempotent,
    resumable, gate_timeout_s)."""

    node_id: str
    accepted_by_change_id: str
    dispatch_kind: Literal["in_process", "job", "inline"] = "in_process"
    limits: dict[str, Any] = Field(default_factory=dict)
    resolved_inputs: Optional[dict[str, Any]] = None
    compiled_prompt: Optional[str] = None
    request: Optional[dict[str, Any]] = None
    spec_digest: Optional[str] = None
    explicit_retry: bool = False


class Checkpoint(_Record):
    run_id: str
    checkpoint_ns: str = ""
    checkpoint_id: str
    parent_checkpoint_id: Optional[str] = None
    attempt_id: str
    type: str
    checkpoint: bytes
    metadata: dict[str, Any]
    writes: list[dict[str, Any]] = Field(default_factory=list)


# ----------------------------------------------------------------------
# The protocol
# ----------------------------------------------------------------------
class GraphStore(Protocol):
    async def create_session(self, prompt: str, *, execution_mode: str = "auto") -> str: ...
    async def set_control_state(self, session_id: str, control_state: str) -> None: ...
    async def get_graph(self, session_id: str) -> GraphView: ...
    async def submit_change(
        self, session_id: str, *, author: str, base_version: int, content: dict[str, Any],
        approval_reasons: tuple[str, ...] = (), tool_call: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
    ) -> ChangeResult: ...
    async def decide_change(self, change_id: str, decision: str, content_digest: str) -> ChangeResult: ...
    async def start_node(self, request: StartRequest) -> StartResult: ...
    async def mark_dispatched(self, run_id: str, attempt_id: str, state: str) -> Attempt: ...
    async def mark_running(self, run_id: str, attempt_id: str) -> Attempt: ...
    async def complete_attempt(self, run_id: str, attempt_id: str, outputs: dict[str, Any]) -> Attempt: ...
    async def fail_attempt(self, run_id: str, attempt_id: str, error: dict[str, Any]) -> Attempt: ...
    async def request_cancel(self, run_id: str, attempt_id: str, reason: str = "user") -> Attempt: ...
    async def confirm_stopped(self, run_id: str, attempt_id: str) -> Attempt: ...
    async def reconcile(self, run_id: str, attempt_id: str, observed: str,
                        outputs: Optional[dict[str, Any]] = None) -> tuple[Attempt, str]: ...
    async def suspend(self, run_id: str, attempt_id: str, suspension: dict[str, Any]) -> Attempt: ...
    async def gate_decision(self, run_id: str, attempt_id: str, interrupt_id: str, decision: str) -> Attempt: ...
    async def resume(self, run_id: str, attempt_id: str) -> Attempt: ...
    async def put_checkpoint(self, run_id: str, attempt_id: str, *, checkpoint_id: str, checkpoint: bytes,
                             type: str, metadata: dict[str, Any], checkpoint_ns: str = "",
                             parent_checkpoint_id: Optional[str] = None) -> None: ...
    async def put_checkpoint_writes(self, run_id: str, attempt_id: str, *, checkpoint_id: str,
                                    writes: list[dict[str, Any]], checkpoint_ns: str = "") -> int: ...
    async def get_checkpoint(self, run_id: str, checkpoint_id: Optional[str] = None,
                             checkpoint_ns: str = "") -> Optional[Checkpoint]: ...
    async def list_checkpoints(self, run_id: str, *, checkpoint_ns: Optional[str] = None,
                               before: Optional[str] = None, limit: int = 20) -> list[Checkpoint]: ...


# ----------------------------------------------------------------------
# Over the Application API
# ----------------------------------------------------------------------
class HttpGraphStore:
    """GraphStore over HTTP. Every invariant is the Application API's."""

    def __init__(self, client: Optional[HarnessClient] = None) -> None:
        self.client = client or HarnessClient()

    async def _call(self, coroutine):
        try:
            return await coroutine
        except ApplicationApiError as exc:
            raise GraphStoreError(exc.code, exc.message, exc.status_code) from exc

    async def create_session(self, prompt: str, *, execution_mode: str = "auto") -> str:
        session = await self._call(self.client.create_session(prompt, execution_mode=execution_mode))
        return session["session_id"]

    async def set_control_state(self, session_id: str, control_state: str) -> None:
        await self._call(self.client.set_control_state(session_id, control_state))

    async def get_graph(self, session_id: str) -> GraphView:
        return GraphView.model_validate(await self._call(self.client.get_graph(session_id)))

    async def submit_change(self, session_id: str, *, author: str, base_version: int, content: dict[str, Any],
                            approval_reasons: tuple[str, ...] = (), tool_call: Optional[dict] = None,
                            idempotency_key: Optional[str] = None) -> ChangeResult:
        body = await self._call(self.client.submit_change(
            session_id, author=author, base_version=base_version, content=content,
            approval_reasons=list(approval_reasons), tool_call=tool_call, idempotency_key=idempotency_key,
        ))
        return ChangeResult.model_validate(body)

    async def decide_change(self, change_id: str, decision: str, content_digest: str) -> ChangeResult:
        return ChangeResult.model_validate(
            await self._call(self.client.decide_change(change_id, decision, content_digest))
        )

    async def start_node(self, request: StartRequest) -> StartResult:
        body = request.model_dump(exclude_none=True)
        return StartResult.model_validate(await self._call(self.client.start_node(body)))

    async def mark_dispatched(self, run_id: str, attempt_id: str, state: str) -> Attempt:
        return Attempt.model_validate(await self._call(self.client.mark_dispatched(run_id, attempt_id, state)))

    async def mark_running(self, run_id: str, attempt_id: str) -> Attempt:
        return Attempt.model_validate(await self._call(self.client.mark_running(run_id, attempt_id)))

    async def complete_attempt(self, run_id: str, attempt_id: str, outputs: dict[str, Any]) -> Attempt:
        return Attempt.model_validate(
            await self._call(self.client.complete_attempt(run_id, attempt_id, outputs))
        )

    async def fail_attempt(self, run_id: str, attempt_id: str, error: dict[str, Any]) -> Attempt:
        return Attempt.model_validate(await self._call(self.client.fail_attempt(run_id, attempt_id, error)))

    async def request_cancel(self, run_id: str, attempt_id: str, reason: str = "user") -> Attempt:
        return Attempt.model_validate(await self._call(self.client.request_cancel(run_id, attempt_id, reason)))

    async def confirm_stopped(self, run_id: str, attempt_id: str) -> Attempt:
        return Attempt.model_validate(await self._call(self.client.confirm_stopped(run_id, attempt_id)))

    async def reconcile(self, run_id: str, attempt_id: str, observed: str,
                        outputs: Optional[dict[str, Any]] = None) -> tuple[Attempt, str]:
        report = {"outputs": outputs} if outputs is not None else {}
        body = await self._call(self.client.reconcile_attempt(run_id, attempt_id, observed, **report))
        return Attempt.model_validate(body["attempt"]), body["action"]

    async def suspend(self, run_id: str, attempt_id: str, suspension: dict[str, Any]) -> Attempt:
        return Attempt.model_validate(await self._call(self.client.suspend_attempt(run_id, attempt_id, suspension)))

    async def gate_decision(self, run_id: str, attempt_id: str, interrupt_id: str, decision: str) -> Attempt:
        return Attempt.model_validate(
            await self._call(self.client.record_gate_decision(run_id, attempt_id, interrupt_id, decision))
        )

    async def resume(self, run_id: str, attempt_id: str) -> Attempt:
        return Attempt.model_validate(await self._call(self.client.resume_attempt(run_id, attempt_id)))

    async def put_checkpoint(self, run_id: str, attempt_id: str, *, checkpoint_id: str, checkpoint: bytes,
                             type: str, metadata: dict[str, Any], checkpoint_ns: str = "",
                             parent_checkpoint_id: Optional[str] = None) -> None:
        await self._call(self.client.put_checkpoint(
            run_id, attempt_id, checkpoint_id=checkpoint_id, checkpoint=checkpoint, type=type,
            metadata=metadata, checkpoint_ns=checkpoint_ns, parent_checkpoint_id=parent_checkpoint_id,
        ))

    async def put_checkpoint_writes(self, run_id: str, attempt_id: str, *, checkpoint_id: str,
                                    writes: list[dict[str, Any]], checkpoint_ns: str = "") -> int:
        return await self._call(self.client.put_checkpoint_writes(
            run_id, attempt_id, checkpoint_id=checkpoint_id, writes=writes, checkpoint_ns=checkpoint_ns,
        ))

    async def get_checkpoint(self, run_id: str, checkpoint_id: Optional[str] = None,
                             checkpoint_ns: str = "") -> Optional[Checkpoint]:
        found = await self._call(self.client.get_checkpoint(run_id, checkpoint_id, checkpoint_ns))
        return Checkpoint.model_validate(found) if found else None

    async def list_checkpoints(self, run_id: str, *, checkpoint_ns: Optional[str] = None,
                               before: Optional[str] = None, limit: int = 20) -> list[Checkpoint]:
        rows = await self._call(self.client.list_checkpoints(
            run_id, checkpoint_ns=checkpoint_ns, before=before, limit=limit
        ))
        # A listing has no blobs; fetch one by id when the bytes are needed.
        return [Checkpoint.model_validate({**row, "type": "", "checkpoint": b""}) for row in rows]
