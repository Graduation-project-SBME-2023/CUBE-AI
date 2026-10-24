"""The node runner: runs one attempt of one graph node, from start to result.

``NodeRunner.run(view, node_id)`` performs the steps shared by every node and
leaves the work itself to the ``NodeHandler`` of the node's kind
(``node_handlers.py``):

1. Resolve the node's target in the catalog and bind each input port to the
   current completed run of its source node. Sources are resolved with
   ``planning.inputs.resolve_inputs``, the same function graph validation
   uses, so a binding always matches what was validated.
2. Start the run with ``GraphStore.start_node``. The store records the run's
   limits, its input bindings and, for an agent, its prompt. If the store
   refuses (``NOT_READY``, ``STALE_DEFINITION``, ``ATTEMPT_ACTIVE``,
   ``NEEDS_RELEASE``, ...), nothing has started and the caller should read the
   graph again.
3. Build a ``ToolContext`` carrying ``run_id`` and ``attempt_id``. Every write
   made by the work identifies its attempt, so the store can refuse writes
   from an attempt that no longer counts.
4. Mark the attempt as running. A job's worker does this itself.
5. Let the handler do the work under the attempt's cancel token. A retry
   attempt of a run whose agent keeps its session (``resumable``) continues
   that session instead of starting over.
6. Extract each output port, validate it against its artifact type and convert
   it to the stored form ``{type, ref: {table, ids}, summary, phi}``
   (``artifact_refs.py``).
7. Record the result with ``complete_attempt`` or ``fail_attempt``. If the
   attempt was cancelled, confirm the stop with ``confirm_stopped`` instead.
   An agent that stopped to wait (a question to the user, a tool approval, a
   pause) is recorded with ``suspend``: the attempt keeps its state in the
   store and its transcript, and nothing runs until it is resumed. If the
   store refuses the suspend because the attempt was cancelled or superseded,
   the stop is confirmed instead. Each of these records carries the model
   tokens the attempt spent.

``NodeRunner.resume(view, node_id)`` continues a suspended attempt once the
store lets it (the question is answered, or the pause ended): the same
attempt runs again from step 5, with the answer.

Status events come from the store's transactions, and a tool's view events
from the dispatcher. The runner sends one event itself: the view of an agent
node that completed, built from its structured output (``agent_views.py``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Optional
from uuid import UUID

from ..planning.inputs import resolve_inputs
from ..toolkit.agents.engine import AgentEngine, Resume, Usage
from ..toolkit.agents.spec import AgentSpec
from ..toolkit.artifacts.base import ArtifactValidationError
from ..toolkit.catalog import Catalog, UnknownRefError
from ..toolkit.context import ToolContext
from ..toolkit.events import EventSink
from ..toolkit.ports import PortExtractError
from ..toolkit.runtime.cancel import CancelToken
from ..toolkit.runtime.dispatch import Dispatcher
from ..toolkit.tools.spec import ToolSpec
from .agent_views import agent_view
from .artifact_refs import StoredRefError, stored_port
from .diff_engine import state_from_view
from .graph_store import GraphStore, GraphStoreError, GraphView, LiveNode, StartRequest, StartResult
from .node_handlers import (
    AgentNodeHandler,
    Failed,
    NodeHandler,
    NodeInput,
    NodeSpec,
    NodeWork,
    NotStartable,
    Outcome,
    Prepared,
    Produced,
    Suspended,
    ToolNodeHandler,
)

logger = logging.getLogger(__name__)

type ReportStatus = Literal["completed", "failed", "cancelled", "suspended", "not_started", "stale"]
type SinkFor = Callable[[str], Optional[EventSink]]

# Store refusals meaning the attempt no longer counts (it was superseded,
# cancelled or ended in the meantime). Its result is dropped; this is not an error.
STALE_CODES = frozenset({"STALE_ATTEMPT", "ATTEMPT_ENDED", "ATTEMPT_SUSPENDED"})
OUTPUT_ERRORS = (PortExtractError, ArtifactValidationError, StoredRefError)
# Suspend refusals meaning the attempt must stop instead of waiting: it has a
# cancel request, or it is no longer a running attempt of a current run.
STOP_INSTEAD_CODES = frozenset({"CANCEL_REQUESTED", "NOT_SUSPENDABLE"})


@dataclass(frozen=True)
class NodeReport:
    """The outcome of one ``NodeRunner.run`` call. ``code`` is the error or refusal code.

    ``stale`` means the attempt ran but was superseded or cancelled before its
    result could be recorded. ``suspended`` means the attempt waits for an
    answer or for the end of a pause; ``code`` is then the suspension's kind.
    """

    node_id: str
    status: ReportStatus
    run_id: Optional[str] = None
    attempt_id: Optional[str] = None
    code: Optional[str] = None
    detail: str = ""
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class _Started:
    node_id: str
    run_id: str
    attempt_id: str

    def report(self, status: ReportStatus, code: Optional[str] = None, detail: str = "",
               usage: Optional[Usage] = None) -> NodeReport:
        return NodeReport(self.node_id, status, self.run_id, self.attempt_id, code, detail, usage or Usage())


def _live(view: GraphView, node_id: str) -> LiveNode:
    for item in view.nodes:
        if item.node.node_id == node_id:
            return item
    raise NotStartable("NOT_FOUND", f"node {node_id} is not live in the graph")


def _port_output(run: Any, port: str) -> Optional[Mapping[str, Any]]:
    return ((run.outputs or {}).get("ports") or {}).get(port) if run is not None else None


def resume_value(suspension: Mapping[str, Any]) -> Resume:
    """How a suspended attempt continues, from its stored suspension.

    A pause continues with nothing to answer. An answered question continues
    with the user's answer; a tool approval or a form with the decision
    (``approve`` or ``reject``).
    """
    if suspension.get("kind") == "pause":
        return Resume.continue_()
    given = suspension.get("resume") or {}
    return Resume(given["answer"] if "answer" in given else given.get("decision"))


def usage_record(usage: Usage) -> Optional[dict[str, Any]]:
    """The stored form of an attempt's usage, or ``None`` when no model tokens were spent."""
    if usage.total_tokens == 0:
        return None
    return {"v": 1, "input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens}


def pin_inputs(view: GraphView, node_id: str, catalog: Catalog) -> dict[str, NodeInput]:
    """Bind each input port of ``node_id`` to the current completed run of its source node.

    Raises ``NotStartable`` when a port has no valid source, or when its source
    has not completed yet; the store would refuse to start the node in both cases.
    """
    sequence = state_from_view(view).sequence
    resolution = resolve_inputs(sequence, UUID(node_id), catalog)
    if not resolution.ok:
        raise NotStartable("INVALID_INPUTS", "; ".join(f"{e.code}: {e.detail}" for e in resolution.errors))

    pinned: dict[str, NodeInput] = {}
    for port, source in resolution.bound.items():
        run = _live(view, str(source.node_id)).run
        stored = _port_output(run, source.port)
        if run is None or run.status != "completed" or stored is None:
            raise NotStartable("NOT_READY", f"input {port!r} waits for node {source.node_id}")
        pinned[port] = NodeInput(
            port=port, artifact_type=stored["type"], ref=stored["ref"], summary=stored.get("summary") or {},
            node_id=str(source.node_id), run_id=run.run_id, upstream_port=source.port,
        )
    return pinned


def frozen_inputs(view: GraphView, started: StartResult) -> dict[str, NodeInput]:
    """The run's inputs as recorded at its first start.

    A retry uses these bindings even if an upstream node has a newer run since.
    Summaries are taken from the view when the bound run is still the upstream
    node's current run, and are empty otherwise.
    """
    current = {item.node.node_id: item.run for item in view.nodes if item.run is not None}
    inputs = {}
    for port, pin in (((started.run.resolved_inputs or {}).get("ports")) or {}).items():
        run = current.get(str(pin["node_id"]))
        stored = _port_output(run, pin["port"]) if run is not None and run.run_id == str(pin["run_id"]) else None
        inputs[port] = NodeInput(
            port=port, artifact_type=pin["type"], ref=pin["ref"], summary=(stored or {}).get("summary") or {},
            node_id=str(pin["node_id"]), run_id=str(pin["run_id"]), upstream_port=pin["port"],
        )
    return inputs


class NodeRunner:
    """Runs graph nodes against a ``GraphStore``, one attempt per ``run`` call."""

    def __init__(
        self,
        store: GraphStore,
        catalog: Catalog,
        *,
        engine: AgentEngine,
        dispatcher: Optional[Dispatcher] = None,
        sink_for: Optional[SinkFor] = None,
        handlers: Optional[Mapping[str, NodeHandler]] = None,
    ) -> None:
        self.store = store
        self.catalog = catalog
        self.sink_for: SinkFor = sink_for or (lambda _session_id: None)
        # One handler per target_kind. Supporting a new kind of node means adding a handler.
        self.handlers: Mapping[str, NodeHandler] = handlers or {
            "agent": AgentNodeHandler(engine, catalog),
            "tool": ToolNodeHandler(dispatcher or Dispatcher(), catalog),
        }

    async def run(
        self,
        view: GraphView,
        node_id: str,
        *,
        cancel: Optional[CancelToken] = None,
        explicit_retry: bool = False,
    ) -> NodeReport:
        """Start ``node_id`` of ``view`` and run its attempt to the end.

        ``cancel`` is the attempt's cancel token. To stop the attempt, the caller
        first requests the cancel from the store, then fires the token.
        ``explicit_retry`` marks a retry the user asked for, which the store
        allows for a cancelled run.
        """
        cancel = cancel or CancelToken()
        try:
            live = _live(view, node_id)
            spec, handler = self._resolve(live)
            inputs = pin_inputs(view, node_id, self.catalog)
            prepared = handler.prepare(live.node, spec, inputs)
        except NotStartable as refusal:
            return NodeReport(node_id, "not_started", code=refusal.code, detail=refusal.detail)

        try:
            started = await self.store.start_node(StartRequest(
                node_id=node_id,
                accepted_by_change_id=live.node.accepted_by_change_id,
                dispatch_kind=prepared.dispatch_kind,
                limits=prepared.limits,
                resolved_inputs={"v": 1, "ports": {port: item.pin() for port, item in inputs.items()}},
                compiled_prompt=prepared.compiled_prompt,
                spec_digest=prepared.spec_digest,
                engine_version=prepared.engine_version,
                explicit_retry=explicit_retry,
            ))
        except GraphStoreError as refusal:  # the store refused the start: nothing has started
            return NodeReport(node_id, "not_started", code=refusal.code, detail=refusal.message)

        attempt = _Started(node_id, started.run.run_id, started.attempt.attempt_id)
        resume = None
        if not started.first_start:  # a retry keeps the inputs of the run's first start
            inputs = frozen_inputs(view, started)
            prepared = handler.prepare(live.node, spec, inputs)
            if started.run.execution_policy.get("resumable"):
                # The agent continues its saved session; with nothing saved, the engine starts afresh.
                resume = Resume.continue_()
        work = NodeWork(
            node=live.node, spec=spec, inputs=inputs, run=started.run, attempt=started.attempt,
            ctx=self._context(view, live, started, prepared, cancel), cancel=cancel, prepared=prepared,
            resume=resume,
        )

        if prepared.dispatch_kind != "job":
            try:
                await self.store.mark_dispatched(attempt.run_id, attempt.attempt_id, "running")
            except GraphStoreError as refusal:
                if refusal.code == "CANCEL_REQUESTED":  # cancelled before it ran
                    return await self._stopped(attempt)
                raise
        return await self._finish(handler, spec, work, attempt)

    async def resume(self, view: GraphView, node_id: str, *, cancel: Optional[CancelToken] = None) -> NodeReport:
        """Continue the suspended attempt of ``node_id`` and run it to the end.

        The attempt is the same one that suspended; no new attempt is opened.
        The store refuses (``NOT_RESUMABLE``) while the question is unanswered,
        while the session is paused, or once the run was superseded or
        cancelled; nothing runs then, and the caller should read the graph
        again. An answered question resumes with the answer, or with the
        decision for a tool approval or a form; a pause resumes with a plain
        continue.
        """
        cancel = cancel or CancelToken()
        try:
            live = _live(view, node_id)
            if live.run is None or live.attempt is None or live.attempt.state != "suspended":
                raise NotStartable("NOT_SUSPENDED", f"node {node_id} has no suspended attempt")
            spec, handler = self._resolve(live)
        except NotStartable as refusal:
            return NodeReport(node_id, "not_started", code=refusal.code, detail=refusal.detail)

        attempt = _Started(node_id, live.run.run_id, live.attempt.attempt_id)
        try:
            # The store's copy, not the view's: the answer may be newer than the view.
            resumed = await self.store.resume(attempt.run_id, attempt.attempt_id)
        except GraphStoreError as refusal:
            return attempt.report("not_started", refusal.code, refusal.message)

        started = StartResult(run=live.run, attempt=resumed, first_start=False)
        inputs = frozen_inputs(view, started)
        prepared = handler.prepare(live.node, spec, inputs)
        work = NodeWork(
            node=live.node, spec=spec, inputs=inputs, run=live.run, attempt=resumed,
            ctx=self._context(view, live, started, prepared, cancel), cancel=cancel, prepared=prepared,
            resume=resume_value(resumed.suspension or {}),
        )
        return await self._finish(handler, spec, work, attempt)

    async def _finish(self, handler: NodeHandler, spec: NodeSpec, work: NodeWork, attempt: _Started) -> NodeReport:
        """Run the attempt's work and record how it ended."""
        cancel = work.cancel
        outcome = await self._execute(handler, work)
        if cancel.cancelled:
            return await self._stopped(attempt, outcome.usage)
        if isinstance(outcome, Suspended):
            return await self._suspend(attempt, outcome)
        if isinstance(outcome, Produced):
            try:
                outputs = self.outputs(spec, outcome.result)
            except OUTPUT_ERRORS as exc:
                outcome = Failed("bad_output", str(exc), outcome.usage)
            else:
                report = await self._complete(attempt, outputs, outcome.usage)
                if report.status == "completed":
                    await self._show(work, outcome.result)
                return report
        return await self._fail(attempt, outcome)

    async def _show(self, work: NodeWork, result: Any) -> None:
        """Send a completed agent's view. Best effort: the result is already recorded."""
        sink = work.ctx.event_sink
        if sink is None or not isinstance(work.spec, AgentSpec):
            return
        try:
            view = agent_view(work.node, work.run.run_id, getattr(result, "output", None), work.inputs)
            if view is not None:
                await sink.emit_view(view)
        except Exception:
            logger.exception("the view of node %s (run %s) was not sent", work.node.node_id, work.run.run_id)

    # ------------------------------------------------------------------
    # steps
    # ------------------------------------------------------------------
    def _resolve(self, live: LiveNode) -> tuple[NodeSpec, NodeHandler]:
        node = live.node
        try:
            spec = self.catalog.resolve(node.target_name)
        except UnknownRefError:
            spec = None
        wanted = AgentSpec if node.target_kind == "agent" else ToolSpec
        if not isinstance(spec, wanted):
            raise NotStartable("UNKNOWN_REF", f"{node.target_kind} {node.target_name!r} is not in the catalog")
        handler = self.handlers.get(node.target_kind)
        if handler is None:
            raise NotStartable("UNKNOWN_KIND", f"no handler runs {node.target_kind} nodes")
        return spec, handler

    def _context(self, view: GraphView, live: LiveNode, started: StartResult, prepared: Prepared,
                 cancel: CancelToken) -> ToolContext:
        return ToolContext(
            session_id=view.session_id,
            caller=prepared.caller,
            node_id=live.node.node_id,
            run_id=started.run.run_id,
            attempt_id=started.attempt.attempt_id,
            agent_type=live.node.target_name if live.node.target_kind == "agent" else None,
            privacy_mode=started.run.execution_policy.get("privacy_mode") or "standard",
            event_sink=self.sink_for(view.session_id),
            cancel=cancel,
        )

    async def _execute(self, handler: NodeHandler, work: NodeWork) -> Outcome:
        try:
            return await handler.execute(work)
        except Exception as exc:  # an unexpected error fails the attempt, not the caller
            logger.exception("node %s (run %s) failed", work.node.node_id, work.run.run_id)
            return Failed("error", str(exc) or type(exc).__name__)

    def outputs(self, spec: NodeSpec, result: Any) -> dict[str, Any]:
        """Build the run's stored outputs from a result.

        Each output port's artifacts are extracted and validated against their
        artifact type. An optional port that produced nothing is left out.
        """
        ports = {}
        for name, port in spec.outputs.items():
            try:
                artifacts = port.extract_artifacts(result)
            except OUTPUT_ERRORS:
                raise
            except Exception as exc:  # an extract that cannot read the result
                raise PortExtractError(f"output {name!r}: {exc}") from exc
            if artifacts:
                artifact_type = self.catalog.artifact_types.get(port.artifact_type)
                checked = [artifact_type.validate_artifact(artifact) for artifact in artifacts]
                ports[name] = stored_port(artifact_type, checked)
        return {"v": 1, "ports": ports}

    async def _complete(self, attempt: _Started, outputs: dict[str, Any], usage: Usage) -> NodeReport:
        try:
            await self.store.complete_attempt(attempt.run_id, attempt.attempt_id, outputs, usage_record(usage))
        except GraphStoreError as refusal:
            if refusal.code in STALE_CODES:
                return attempt.report("stale", refusal.code, refusal.message, usage)
            if refusal.code == "INVALID_OUTPUTS":  # the store refused the output refs
                return await self._fail(attempt, Failed("bad_output", refusal.message, usage))
            raise
        return attempt.report("completed", usage=usage)

    async def _fail(self, attempt: _Started, outcome: Failed) -> NodeReport:
        error = {"code": outcome.code, "message": outcome.message[:2000]}
        try:
            await self.store.fail_attempt(attempt.run_id, attempt.attempt_id, error, usage_record(outcome.usage))
        except GraphStoreError as refusal:
            if refusal.code in STALE_CODES:
                return attempt.report("stale", refusal.code, refusal.message, outcome.usage)
            raise
        return attempt.report("failed", outcome.code, outcome.message, outcome.usage)

    async def _suspend(self, attempt: _Started, outcome: Suspended) -> NodeReport:
        """Record that the attempt waits, or confirm its stop if it may not wait.

        A suspend that is sent again is accepted by the store as the same
        suspension, so a retried call is safe.
        """
        suspension = outcome.suspension
        body: dict[str, Any] = {"kind": suspension.kind.value, "payload": dict(suspension.payload)}
        if suspension.tool_use_id is not None:
            body["tool_use_id"] = suspension.tool_use_id
        try:
            await self.store.suspend(attempt.run_id, attempt.attempt_id, body)
        except GraphStoreError as refusal:
            if refusal.code not in STOP_INSTEAD_CODES:
                raise
            return await self._stopped(attempt, outcome.usage)
        return attempt.report("suspended", suspension.kind.value, usage=outcome.usage)

    async def _stopped(self, attempt: _Started, usage: Optional[Usage] = None) -> NodeReport:
        """Confirm to the store that a cancelled attempt has stopped.

        If the cancel was never requested from the store (for example during a
        shutdown), it is requested first, so the stop is always recorded.
        """
        spent = usage_record(usage or Usage())
        try:
            stopped = await self.store.confirm_stopped(attempt.run_id, attempt.attempt_id, spent)
        except GraphStoreError as refusal:
            if refusal.code != "NO_CANCEL_REQUEST":
                raise
            await self.store.request_cancel(attempt.run_id, attempt.attempt_id, reason="harness")
            stopped = await self.store.confirm_stopped(attempt.run_id, attempt.attempt_id, spent)
        code = (stopped.error or {}).get("code")
        return attempt.report("failed" if stopped.state == "failed" else "cancelled", code, usage=usage)
