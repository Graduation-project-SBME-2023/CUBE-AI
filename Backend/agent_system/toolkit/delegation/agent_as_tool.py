"""make_agent_tool: a control agent behind a Main-visible tool
(merged plan, Phase 1.4 step 5).

``plan`` (the Composer, Plan 12) and ``recover`` (the Recovery Agent, Plan 13)
are short calls: the Main Agent asks, the control agent answers with a
proposal, and nothing is executed. The tool runs the agent once, validates its
output against ``output_contract``, and hands it to ``post``, a deterministic
follow-up such as the Validator. ``post`` may ask the agent again with
feedback, which is how ``plan`` repairs a proposal the Validator rejects.

The hard timeout is the tool's ``timeout_s``: dispatch enforces it over the
whole call, repair rounds included.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Any

from pydantic import BaseModel, Field, ValidationError

from ..agents.engine import AgentStatus
from ..agents.spec import AgentKind
from ..context import ToolContext
from ..results import error_result
from ..tools.spec import ToolSpec
from .run_agent import agent_runtime, failed_result, invalid_output_result, no_runtime_result

DEFAULT_AGENT_TOOL_TIMEOUT_S = 300.0


class AgentRequest(BaseModel):
    """The default arguments: one request in plain words."""

    request: Annotated[str, Field(description="What you need, in plain words.")]


# Runs the agent again with feedback added to its task; returns its validated output.
type Ask = Callable[[str], Awaitable[BaseModel]]
# The deterministic follow-up: (validated output, ask, ctx) -> what the tool returns.
type Post = Callable[[BaseModel, Ask, ToolContext], Awaitable[BaseModel | Mapping[str, Any]]]


class _AgentCallFailed(Exception):
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result


def make_agent_tool(
    name: str,
    agent_type: str,
    output_contract: type[BaseModel],
    post: Post | None = None,
    *,
    arguments: type[BaseModel] = AgentRequest,
    description: str | None = None,
    **manifest: Any,
) -> ToolSpec:
    """A ``main``-visible inline tool that runs the control agent ``agent_type``.

    The agent gets ``description`` as its task and the call's arguments as its
    inputs. ``manifest`` overrides the defaults below (a tool whose ``post``
    stores a proposal sets ``side_effects="writes"`` and ``db_write``, for one).
    """
    description = description or (
        f"Ask {agent_type} for a {output_contract.__name__}. "
        "It only proposes; nothing is executed."
    )

    async def handler(ctx: ToolContext, **args: Any) -> Any:
        runtime = agent_runtime(ctx)
        if runtime is None:
            return no_runtime_result()
        if agent_type not in runtime.agents:
            return error_result("unknown_agent", f"{name} needs agent {agent_type!r}")
        agent = runtime.agents.get(agent_type)
        if agent.kind is not AgentKind.CONTROL:
            return error_result("not_a_control_agent", f"{agent_type!r} is not a control agent")
        inputs = arguments.model_validate(args).model_dump(mode="json")
        agent_ctx = ctx.for_subagent(agent_type)

        async def ask(feedback: str | None = None) -> BaseModel:
            task = description if feedback is None else f"{description}\n\n{feedback}"
            result = await runtime.engine.run(agent, task, inputs, agent_ctx, ctx.cancel)
            if result.status is not AgentStatus.COMPLETED:
                raise _AgentCallFailed(failed_result(agent_type, result))
            try:
                return output_contract.model_validate(result.output)
            except ValidationError as exc:
                raise _AgentCallFailed(invalid_output_result(agent_type, exc)) from exc

        try:
            output: BaseModel | Mapping[str, Any] = await ask()
            if post is not None:
                output = await post(output, ask, ctx)
        except _AgentCallFailed as failure:
            return failure.result
        return output.model_dump(mode="json") if isinstance(output, BaseModel) else dict(output)

    defaults: dict[str, Any] = {
        "pack": "control",
        "side_effects": "none",
        "read_only": True,
        "cost": "medium",
        "execution": "inline",
        "visibility": {"main"},
        "resources": {"llm"},
        "timeout_s": DEFAULT_AGENT_TOOL_TIMEOUT_S,
    }
    return ToolSpec(
        name=name,
        description=description,
        handler=handler,
        input_model=arguments,
        context_param="ctx",
        **(defaults | manifest),
    )
