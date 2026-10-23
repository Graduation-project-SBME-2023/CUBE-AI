"""Node handlers: the part of running a node that depends on its kind.

``NodeRunner`` (``node_runner.py``) performs the steps shared by every node:
it pins the inputs, starts the run, builds the ``ToolContext``, marks the
attempt as running, extracts the outputs and records the result. A
``NodeHandler`` supplies the rest in two calls:

- ``prepare``, before the run starts, returns the attempt's dispatch kind, the
  limits recorded on the run and, for an agent, its prompt and spec digest.
  Raising ``NotStartable`` keeps the node from starting.
- ``execute``, once the attempt is running, does the work and returns
  ``Produced`` (the result the output ports read) or ``Failed`` (an error code).

The runner selects a handler by the node's ``target_kind``. A job is not a
kind of node: it is a way of running a tool (``execution="job"``), routed by
the dispatcher.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

from ..toolkit.agents.engine import AgentEngine, AgentStatus, Usage
from ..toolkit.agents.spec import AgentSpec
from ..toolkit.catalog import Catalog
from ..toolkit.context import Caller, ToolContext
from ..toolkit.manifest import Execution
from ..toolkit.results import is_error_result
from ..toolkit.runtime.cancel import CancelToken
from ..toolkit.runtime.dispatch import Dispatcher
from ..toolkit.tools.spec import ToolSpec
from .artifact_refs import artifact_refs, pointer
from .graph_store import Attempt, Node, Run

type NodeSpec = ToolSpec | AgentSpec
type DispatchKind = Literal["in_process", "inline", "job"]

# The total number of attempts an agent run may have, retries included.
# AGENT_NODE_MAX_ATTEMPTS overrides it.
DEFAULT_AGENT_MAX_ATTEMPTS = 3


def agent_max_attempts() -> int:
    """The attempt limit for agent runs, read from the environment on each call."""
    try:
        value = int(os.environ.get("AGENT_NODE_MAX_ATTEMPTS", DEFAULT_AGENT_MAX_ATTEMPTS))
    except ValueError:
        return DEFAULT_AGENT_MAX_ATTEMPTS
    return value if value >= 1 else DEFAULT_AGENT_MAX_ATTEMPTS


class NotStartable(Exception):
    """Raised when a node cannot start. Nothing has been written; ``code`` says why."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class NodeInput:
    """One input port, bound to the stored output of an upstream run."""

    port: str
    artifact_type: str
    ref: Mapping[str, Any]  # {table, ids}
    summary: Mapping[str, Any]
    node_id: str
    run_id: str
    upstream_port: str

    def pin(self) -> dict[str, str]:
        """The ``resolved_inputs`` entry the store checks and records when the run starts."""
        return {"node_id": self.node_id, "run_id": self.run_id, "port": self.upstream_port}


@dataclass(frozen=True)
class Prepared:
    """What ``prepare`` decides before the run starts."""

    dispatch_kind: DispatchKind
    caller: Caller
    limits: dict[str, Any]
    compiled_prompt: str | None = None
    spec_digest: str | None = None
    engine_version: str | None = None


@dataclass(frozen=True)
class NodeWork:
    """One attempt, ready to run."""

    node: Node
    spec: NodeSpec
    inputs: Mapping[str, NodeInput]
    run: Run
    attempt: Attempt
    ctx: ToolContext
    cancel: CancelToken
    prepared: Prepared


@dataclass(frozen=True)
class Produced:
    result: Any  # what the output ports' extract functions read
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class Failed:
    code: str
    message: str = ""
    usage: Usage = field(default_factory=Usage)


type Outcome = Produced | Failed


class NodeHandler(Protocol):
    def prepare(self, node: Node, spec: NodeSpec, inputs: Mapping[str, NodeInput]) -> Prepared: ...

    async def execute(self, work: NodeWork) -> Outcome: ...


# ----------------------------------------------------------------------
# Agent Node
# ----------------------------------------------------------------------
def spec_digest(spec: AgentSpec) -> str:
    """SHA-256 of the parts of an agent that decide whether a saved session can be reused.

    These are the prompt, the tool names, the delegates and the version. A
    prompt or an allowlist computed at run time is represented by the
    qualified name of the function that computes it.
    """

    def part(value: Any) -> Any:
        return value if not callable(value) else f"fn:{value.__module__}.{value.__qualname__}"

    document = {
        "agent_type": spec.agent_type,
        "version": spec.version,
        "prompt": part(spec.system_prompt),
        "tools": part(spec.allowed_tools) if callable(spec.allowed_tools) else list(spec.allowed_tools),
        "delegates": list(spec.delegates),
    }
    return hashlib.sha256(json.dumps(document, sort_keys=True).encode("utf-8")).hexdigest()


def render_node_inputs(task: str, inputs: Mapping[str, Mapping[str, Any]]) -> str:
    """Return the agent's task followed by a "Node inputs" section.

    The section lists each input's type, refs and summary, so the agent finds
    its upstream data in its first message.
    """
    if not inputs:
        return task
    lines = [task.rstrip(), "", "## Node inputs"]
    for port, item in inputs.items():
        ref = json.dumps(item["refs"], sort_keys=True, default=str)
        summary = json.dumps(item["summary"], sort_keys=True, default=str)
        lines.append(f"- {port} ({item['type']}): ref {ref}; summary {summary}")
    return "\n".join(lines)


class AgentNodeHandler:
    """Runs an agent node through the ``AgentEngine``."""

    def __init__(self, engine: AgentEngine, catalog: Catalog) -> None:
        self.engine = engine
        self.catalog = catalog

    def agent_inputs(self, inputs: Mapping[str, NodeInput]) -> dict[str, dict[str, Any]]:
        """For each input port: its artifact type, its artifact refs and its summary."""
        shown = {}
        for port, item in inputs.items():
            artifact_type = self.catalog.artifact_types.get(item.artifact_type)
            shown[port] = {
                "type": item.artifact_type,
                "refs": artifact_refs(artifact_type, item.ref, item.summary),
                "summary": dict(item.summary),
            }
        return shown

    def prepare(self, node: Node, spec: AgentSpec, inputs: Mapping[str, NodeInput]) -> Prepared:
        task = str(node.params.get("task") or "")
        return Prepared(
            dispatch_kind="in_process",
            caller=Caller.AGENT,
            limits={
                "max_attempts": agent_max_attempts(),
                "timeout_s": spec.timeout_s,
                "idempotent": False,
                "resumable": spec.resumable,
                "gate_timeout_s": spec.gate_timeout_s,
            },
            compiled_prompt=render_node_inputs(task, self.agent_inputs(inputs)),
            spec_digest=spec_digest(spec),
        )

    async def execute(self, work: NodeWork) -> Outcome:
        spec = work.spec
        inputs = self.agent_inputs(work.inputs)
        message = work.prepared.compiled_prompt or ""
        if spec.context_builder is not None:
            message = spec.context_builder(str(work.node.params.get("task") or ""), inputs, work.ctx)
        result = await self.engine.run(spec, message, inputs, work.ctx, work.cancel)
        if result.status is not AgentStatus.COMPLETED:
            return Failed(result.error_code or "error", result.summary, result.usage)
        if spec.on_complete is not None:
            # The hook records what the output refers to and returns the run's output.
            # It may run again on a retry, so it must be idempotent.
            result = replace(result, output=await spec.on_complete(work.ctx, result.output))
        return Produced(result, result.usage)


# ----------------------------------------------------------------------
# Tool Node
# ----------------------------------------------------------------------
class ToolNodeHandler:
    """Runs a tool node through the ``Dispatcher``, with the node's params and input refs."""

    def __init__(self, dispatcher: Dispatcher, catalog: Catalog) -> None:
        self.dispatcher = dispatcher
        self.catalog = catalog

    def prepare(self, node: Node, spec: ToolSpec, inputs: Mapping[str, NodeInput]) -> Prepared:
        if spec.execution not in self.dispatcher.runners:
            raise NotStartable("execution_unavailable", f"no runner is registered for {spec.execution} tools")
        return Prepared(
            dispatch_kind="inline" if spec.execution is Execution.INLINE else "job",
            caller=Caller.NODE,
            limits={
                "max_attempts": spec.max_attempts,
                "timeout_s": spec.timeout_s,
                "idempotent": spec.idempotent,
                "resumable": False,
            },
        )

    def arguments(self, node: Node, spec: ToolSpec, inputs: Mapping[str, NodeInput]) -> dict[str, Any]:
        """The node's params, plus the ref of each input port under its ``param`` name."""
        args = dict(node.params)
        for port_name, port in spec.inputs.items():
            item = inputs.get(port_name)
            if item is None or port.param is None:
                continue
            artifact_type = self.catalog.artifact_types.get(item.artifact_type)
            args[port.param] = pointer(artifact_type, item.ref, many=port.many)
        return args

    async def execute(self, work: NodeWork) -> Outcome:
        spec = work.spec
        args = self.arguments(work.node, spec, work.inputs)
        result = await self.dispatcher.invoke(spec, args, work.ctx, work.cancel)
        if is_error_result(result):
            return Failed(result["error_code"], result.get("error_message", ""))
        return Produced(result)
