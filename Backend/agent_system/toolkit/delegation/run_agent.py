"""run_agent: an agent starts one of its internal agents (merged plan, Phase 1.4 step 4).

Delegation is a tool, and only execution and internal agents have it: the
Main Agent never waits on work, it submits nodes instead. ``run_agent`` runs
inside a node that is already tracked, so the child is part of that node's
run, not a node of its own.

- **Per-caller binding:** the tool each agent gets (``ToolRegistry.for_agent``)
  offers only that agent's own ``delegates`` in its ``agent_type`` enum, and
  ``delegate()`` checks it again, since a model can send anything.
- The child runs with ``subagent=<type>`` and ``depth + 1`` (capped by
  ``MAX_AGENT_DEPTH``, default 2), under its own ``timeout_s``, with the
  caller's cancel token. Its events keep the node's ``source`` and carry the
  ``subagent``.
- The child's output is validated against its ``output_contract``.

``delegate()`` is the core. Code running inside a node that starts internal
agents without a model tool call (the segmentation module, Plan 9.4) calls it
directly, so depth, events and cancel work the same way.
"""

from __future__ import annotations

import asyncio
import copy
import os
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import Field, ValidationError, create_model

from ..agents.engine import AgentEngine, AgentResult, AgentStatus
from ..agents.registry import AgentRegistry
from ..agents.spec import AgentKind, AgentSpec
from ..context import ToolContext
from ..results import error_result, validation_message
from ..runtime.cancel import CancelToken
from ..tools.registry import tool
from ..tools.spec import ToolSpec

DEFAULT_MAX_AGENT_DEPTH = 2
# run_agent's own ceiling; the child's timeout_s is the limit that normally applies.
RUN_AGENT_TIMEOUT_S = 6 * 3600.0

AGENT_TYPE_DOC = "The internal agent to run."
TASK_DOC = "What the agent should do, in plain words."
INPUTS_DOC = "Data the agent needs, keyed by name."


@runtime_checkable
class AgentRuntime(Protocol):
    """What delegation needs from the session runtime (``ToolContext.session_handle``)."""

    agents: AgentRegistry
    engine: AgentEngine


def agent_runtime(ctx: ToolContext) -> AgentRuntime | None:
    """The session's agent runtime, when the harness provided one."""
    return ctx.session_handle if isinstance(ctx.session_handle, AgentRuntime) else None


def no_runtime_result() -> dict[str, Any]:
    return error_result("delegation_unavailable", "this session cannot start agents")


def failed_result(agent_type: str, result: AgentResult) -> dict[str, Any]:
    """The error result for a child run that did not complete."""
    reason = result.summary or f"{agent_type} {result.status}"
    return error_result(result.error_code or "error", reason, agent_type=agent_type)


def invalid_output_result(agent_type: str, exc: ValidationError) -> dict[str, Any]:
    return error_result(
        "invalid_output",
        f"{agent_type} output does not match its contract: {validation_message(exc)}",
        agent_type=agent_type,
    )


def max_agent_depth() -> int:
    """``MAX_AGENT_DEPTH``, read at call time; a missing or invalid value means the default."""
    try:
        depth = int(os.environ.get("MAX_AGENT_DEPTH", DEFAULT_MAX_AGENT_DEPTH))
    except ValueError:
        return DEFAULT_MAX_AGENT_DEPTH
    return depth if depth >= 1 else DEFAULT_MAX_AGENT_DEPTH


async def delegate(
    runtime: AgentRuntime,
    agent_type: str,
    task: str,
    inputs: Mapping[str, Any],
    ctx: ToolContext,
    cancel: CancelToken | None = None,
) -> dict[str, Any]:
    """Run ``agent_type`` for the agent ``ctx`` belongs to (its subagent, if
    one is running, else the node's agent) and return run_agent's result."""
    caller = ctx.subagent or ctx.agent_type
    if caller is None or caller not in runtime.agents:
        return error_result("not_a_delegate", "only an agent can start an internal agent")
    delegates = runtime.agents.get(caller).delegates
    if agent_type not in delegates or agent_type not in runtime.agents:
        return error_result(
            "not_a_delegate",
            f"{caller} may not start {agent_type!r}; it may start {list(delegates)}",
        )
    target = runtime.agents.get(agent_type)
    if target.kind is not AgentKind.INTERNAL:
        return error_result("not_a_delegate", f"{agent_type!r} is not an internal agent")
    limit = max_agent_depth()
    if ctx.depth + 1 > limit:
        return error_result("max_depth", f"internal agents may nest at most {limit} deep")

    scope = asyncio.timeout(target.timeout_s)
    try:
        async with scope:
            result = await runtime.engine.run(
                target, task, inputs, ctx.for_subagent(agent_type), cancel
            )
    except TimeoutError:
        if not scope.expired():
            raise
        return error_result(
            "timeout", f"{agent_type} did not finish within {target.timeout_s:g} s",
            agent_type=agent_type,
        )  # fmt: skip

    if result.status is not AgentStatus.COMPLETED:
        return failed_result(agent_type, result)
    try:
        output = _checked_output(target, result.output)
    except ValidationError as exc:
        return invalid_output_result(agent_type, exc)
    return {
        "status": "completed",
        "agent_type": agent_type,
        "summary": result.summary,
        "output": output,
    }


def _checked_output(target: AgentSpec, output: Any) -> Any:
    if target.output_contract is None:
        return output
    return target.output_contract.model_validate(output).model_dump(mode="json")


def _bind(spec: ToolSpec, caller: AgentSpec) -> ToolSpec:
    """run_agent as ``caller`` sees it: ``agent_type`` offers only its delegates."""
    if not caller.delegates or spec.input_model is None:
        return spec
    # A single-value Literal becomes "const" in the schema; state the enum
    # explicitly so every chat-model tool API reads the choice the same way.
    choices = Field(description=AGENT_TYPE_DOC, json_schema_extra={"enum": list(caller.delegates)})
    agent_type = Annotated[Literal[*caller.delegates], choices]
    arguments = create_model(
        spec.input_model.__name__, __base__=spec.input_model, agent_type=(agent_type, ...)
    )
    return copy.replace(spec, input_model=arguments)


@tool(
    pack="delegation",
    side_effects="compute",
    cost="medium",
    execution="inline",
    visibility={"agent"},
    resources={"llm"},
    timeout_s=RUN_AGENT_TIMEOUT_S,
    for_caller=_bind,
)
async def run_agent(
    agent_type: Annotated[str, Field(description=AGENT_TYPE_DOC)],
    task: Annotated[str, Field(description=TASK_DOC)],
    ctx: ToolContext,
    inputs: Annotated[dict[str, Any] | None, Field(description=INPUTS_DOC)] = None,
) -> dict[str, Any]:
    """Run one of your internal agents on a sub-task and return its result."""
    runtime = agent_runtime(ctx)
    if runtime is None:
        return no_runtime_result()
    return await delegate(runtime, agent_type, task, inputs or {}, ctx, ctx.cancel)
