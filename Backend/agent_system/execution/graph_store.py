"""GraphStore: the Executor's typed view of the session graph.

``GraphStore`` is the protocol the Executor programs against. ``HttpGraphStore``
is the real one, over the Application API (which owns every invariant).
``InMemoryGraphStore`` (``in_memory_graph_store.py``) is the test double and is
never looser than the database: the same scenario table runs against both.

Refusals are ``GraphStoreError`` with the Application API's stable codes
(STALE_BASE, NOT_READY, NEEDS_RELEASE, USER_RELEASE_REQUIRED, ATTEMPT_ACTIVE,
MAX_ATTEMPTS, ...).

Agent memory: an agent-node run keeps its Claude Agent SDK transcript as opaque
entries, written only by the run's latest running or suspended attempt and
never parsed here. The SDK's session id (``sdk_session_id``) is the run id for
an agent node and the CUBE.AI ``session_id`` for the Main; ``session_id`` alone
always means the CUBE.AI session. An attempt lists the tool calls it is running
in ``pending_tools``, so a restart knows whether a retry is safe: a call that
was cut off and is not idempotent must not run again automatically.
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
    # {v: 2, kind: ask_user|tool_approval|pause, tool_use_id?, form?, payload, since, resume?}
    suspension: Optional[dict[str, Any]] = None
    # {v: 1, calls: [{tool_use_id, tool, idempotent, started_at}]}
    pending_tools: Optional[dict[str, Any]] = None
    dispatched_at: Optional[datetime] = None
    cancel_requested_at: Optional[datetime] = None
    resumed_at: Optional[datetime] = None
    error: Optional[dict[str, Any]] = None
    # {v: 1, input_tokens, output_tokens, model?}, written when the attempt ends
    usage: Optional[dict[str, Any]] = None
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
    # D28: `before` waits for a user release; `after` holds the next node for a user review.
    approval: Literal["none", "before", "after"] = "none"
    start_released_by_change_id: Optional[str] = None
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


class NextStep(_Record):
    """What the Executor does next: ``start`` the node, ``resume`` its suspended
    attempt, or ``await_release`` (the node waits for the user to release it)."""

    action: Literal["start", "resume", "await_release"]
    node_id: str


class GraphView(_Record):
    session_id: str
    version: int
    nodes: list[LiveNode] = Field(default_factory=list)
    pending_change: Optional[Change] = None
    activity: str
    # None while nothing can happen until the graph, the session or an attempt changes.
    next_step: Optional[NextStep] = None


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
    engine_version: Optional[str] = None
    explicit_retry: bool = False


# ----------------------------------------------------------------------
# The protocol
# ----------------------------------------------------------------------
class GraphStore(Protocol):
    async def create_session(self, prompt: str, *, execution_mode: str = "auto") -> str: ...
    async def set_control_state(self, session_id: str, control_state: str) -> None: ...
    async def get_graph(self, session_id: str) -> GraphView: ...
    async def node_runs(self, node_id: str) -> list[Run]: ...
    async def submit_change(
        self, session_id: str, *, author: str, base_version: int, content: dict[str, Any],
        approval_reasons: tuple[str, ...] = (), tool_call: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
    ) -> ChangeResult: ...
    async def decide_change(self, change_id: str, decision: str, content_digest: str) -> ChangeResult: ...
    async def start_node(self, request: StartRequest) -> StartResult: ...
    async def mark_dispatched(self, run_id: str, attempt_id: str, state: str) -> Attempt: ...
    async def mark_running(self, run_id: str, attempt_id: str) -> Attempt: ...
    # `usage` is the model tokens the attempt spent, added to the session's budget.
    async def complete_attempt(self, run_id: str, attempt_id: str, outputs: dict[str, Any],
                               usage: Optional[dict[str, Any]] = None) -> Attempt: ...
    async def fail_attempt(self, run_id: str, attempt_id: str, error: dict[str, Any],
                           usage: Optional[dict[str, Any]] = None) -> Attempt: ...
    async def request_cancel(self, run_id: str, attempt_id: str, reason: str = "user") -> Attempt: ...
    async def confirm_stopped(self, run_id: str, attempt_id: str,
                              usage: Optional[dict[str, Any]] = None) -> Attempt: ...
    async def reconcile(self, run_id: str, attempt_id: str, observed: str,
                        outputs: Optional[dict[str, Any]] = None) -> tuple[Attempt, str]: ...
    # T10: suspend (harness), answer (the user), resume (harness); the same attempt throughout.
    async def suspend(self, run_id: str, attempt_id: str, suspension: dict[str, Any]) -> Attempt: ...
    async def answer(self, run_id: str, attempt_id: str, tool_use_id: str, *, answer: Any = None,
                     decision: Optional[str] = None, comment: Optional[str] = None) -> Attempt: ...
    async def resume(self, run_id: str, attempt_id: str) -> Attempt: ...
    async def set_pending_tools(self, run_id: str, attempt_id: str, calls: list[dict[str, Any]]) -> Attempt: ...
    # Agent-node transcripts (D27). A read may present the reading attempt
    # (its own run's, or the first attempt of a run seeded from this one).
    async def append_transcript(self, run_id: str, attempt_id: str, entries: list[dict[str, Any]], *,
                                project_key: str, subpath: str = "") -> int: ...
    async def load_transcript(self, run_id: str, *, subpath: str = "",
                              attempt_id: Optional[str] = None) -> Optional[list[dict[str, Any]]]: ...
    async def list_transcript_subpaths(self, run_id: str) -> list[str]: ...
    # The Main's saved session (D21), one per CUBE.AI session.
    async def append_main_transcript(self, session_id: str, entries: list[dict[str, Any]], *,
                                     project_key: str, subpath: str = "") -> int: ...
    async def load_main_transcript(self, session_id: str, *,
                                   subpath: str = "") -> Optional[list[dict[str, Any]]]: ...
    async def list_main_transcript_subpaths(self, session_id: str) -> list[str]: ...
    async def purge_transcripts(self) -> dict[str, int]: ...


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

    async def node_runs(self, node_id: str) -> list[Run]:
        return [Run.model_validate(run) for run in await self._call(self.client.node_runs(node_id))]

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

    async def complete_attempt(self, run_id: str, attempt_id: str, outputs: dict[str, Any],
                               usage: Optional[dict[str, Any]] = None) -> Attempt:
        return Attempt.model_validate(
            await self._call(self.client.complete_attempt(run_id, attempt_id, outputs, usage))
        )

    async def fail_attempt(self, run_id: str, attempt_id: str, error: dict[str, Any],
                           usage: Optional[dict[str, Any]] = None) -> Attempt:
        return Attempt.model_validate(
            await self._call(self.client.fail_attempt(run_id, attempt_id, error, usage))
        )

    async def request_cancel(self, run_id: str, attempt_id: str, reason: str = "user") -> Attempt:
        return Attempt.model_validate(await self._call(self.client.request_cancel(run_id, attempt_id, reason)))

    async def confirm_stopped(self, run_id: str, attempt_id: str,
                              usage: Optional[dict[str, Any]] = None) -> Attempt:
        return Attempt.model_validate(
            await self._call(self.client.confirm_stopped(run_id, attempt_id, usage))
        )

    async def reconcile(self, run_id: str, attempt_id: str, observed: str,
                        outputs: Optional[dict[str, Any]] = None) -> tuple[Attempt, str]:
        report = {"outputs": outputs} if outputs is not None else {}
        body = await self._call(self.client.reconcile_attempt(run_id, attempt_id, observed, **report))
        return Attempt.model_validate(body["attempt"]), body["action"]

    async def suspend(self, run_id: str, attempt_id: str, suspension: dict[str, Any]) -> Attempt:
        return Attempt.model_validate(await self._call(self.client.suspend_attempt(run_id, attempt_id, suspension)))

    async def answer(self, run_id: str, attempt_id: str, tool_use_id: str, *, answer: Any = None,
                     decision: Optional[str] = None, comment: Optional[str] = None) -> Attempt:
        return Attempt.model_validate(await self._call(self.client.answer_attempt(
            run_id, attempt_id, tool_use_id, answer=answer, decision=decision, comment=comment,
        )))

    async def resume(self, run_id: str, attempt_id: str) -> Attempt:
        return Attempt.model_validate(await self._call(self.client.resume_attempt(run_id, attempt_id)))

    async def set_pending_tools(self, run_id: str, attempt_id: str, calls: list[dict[str, Any]]) -> Attempt:
        return Attempt.model_validate(await self._call(self.client.set_pending_tools(run_id, attempt_id, calls)))

    async def append_transcript(self, run_id: str, attempt_id: str, entries: list[dict[str, Any]], *,
                                project_key: str, subpath: str = "") -> int:
        body = await self._call(self.client.append_transcript(
            run_id, attempt_id, entries, project_key=project_key, subpath=subpath,
        ))
        return body["stored"]

    async def load_transcript(self, run_id: str, *, subpath: str = "",
                              attempt_id: Optional[str] = None) -> Optional[list[dict[str, Any]]]:
        return await self._call(self.client.load_transcript(run_id, subpath=subpath, attempt_id=attempt_id))

    async def list_transcript_subpaths(self, run_id: str) -> list[str]:
        return await self._call(self.client.list_transcript_subpaths(run_id))

    async def append_main_transcript(self, session_id: str, entries: list[dict[str, Any]], *,
                                     project_key: str, subpath: str = "") -> int:
        body = await self._call(self.client.append_main_transcript(
            session_id, entries, project_key=project_key, subpath=subpath,
        ))
        return body["stored"]

    async def load_main_transcript(self, session_id: str, *,
                                   subpath: str = "") -> Optional[list[dict[str, Any]]]:
        return await self._call(self.client.load_main_transcript(session_id, subpath=subpath))

    async def list_main_transcript_subpaths(self, session_id: str) -> list[str]:
        return await self._call(self.client.list_main_transcript_subpaths(session_id))

    async def purge_transcripts(self) -> dict[str, int]:
        return await self._call(self.client.purge_transcripts())
