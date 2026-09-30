"""make_agent_tool (merged plan, Phase 1.4 step 5)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import pytest
from pydantic import BaseModel

from agent_system.toolkit.agents.engine import AgentEngine, AgentResult, FakeAgentEngine
from agent_system.toolkit.agents.registry import AgentRegistry
from agent_system.toolkit.agents.spec import AgentSpec
from agent_system.toolkit.catalog import Catalog
from agent_system.toolkit.context import ToolContext
from agent_system.toolkit.delegation.agent_as_tool import make_agent_tool
from agent_system.toolkit.events import ToolEvent, ViewEvent
from agent_system.toolkit.results import error_result
from agent_system.toolkit.runtime.cancel import CancelToken
from agent_system.toolkit.runtime.dispatch import Dispatcher
from agent_system.toolkit.tools.registry import ToolRegistry, spec_of, tool, tools_in

pytestmark = pytest.mark.unit


class GraphProposal(BaseModel):
    summary: str
    nodes: list[str]


class RecoverArgs(BaseModel):
    node_id: str


def _agent(name: str, kind: str, **fields: Any) -> AgentSpec:
    return AgentSpec(
        agent_type=name, description=f"{name}.", kind=kind, system_prompt="Prompt.", **fields
    )


COMPOSER = _agent("composer", "control", output_contract=GraphProposal)
RECOVERY = _agent("recovery_agent", "control")
SEGMENTATION = _agent("segmentation_agent", "execution")
PLAN = make_agent_tool("plan", "composer", GraphProposal)


@dataclass
class _Runtime:
    agents: AgentRegistry
    engine: AgentEngine


def _runtime(**scripts: Any) -> _Runtime:
    return _Runtime(AgentRegistry([COMPOSER, RECOVERY, SEGMENTATION]), FakeAgentEngine(scripts))


def _ctx(runtime: Any, **fields: Any) -> ToolContext:
    return ToolContext(
        session_id="s1", caller="main", agent_type="main_agent", session_handle=runtime, **fields
    )


def _call(spec: Any, args: dict[str, Any], ctx: ToolContext, cancel: Any = None) -> Any:
    return asyncio.run(Dispatcher().invoke(spec, args, ctx, cancel))


def _proposal(**overrides: Any) -> AgentResult:
    output = {"summary": "Segment, then extract features.", "nodes": ["seg", "features"]}
    return AgentResult(status="completed", output=output | overrides)


# --- the tool


def test_the_tool_is_a_main_visible_inline_medium_tool():
    assert PLAN.visibility == {"main"}
    assert (PLAN.execution, PLAN.cost, PLAN.pack) == ("inline", "medium", "control")
    assert PLAN.resources == {"llm"}
    assert PLAN.read_only and PLAN.side_effects == "none"
    assert PLAN.timeout_s == 300
    assert PLAN.arg_names == {"request"}
    assert PLAN.description == (
        "Ask composer for a GraphProposal. It only proposes; nothing is executed."
    )


def test_manifest_and_arguments_can_be_overridden():
    recover = make_agent_tool(
        "recover",
        "recovery_agent",
        GraphProposal,
        arguments=RecoverArgs,
        description="Diagnose a failed node and propose a fix.",
        side_effects="writes",
        read_only=False,
        data_scopes={"db_write"},
        timeout_s=120,
    )

    assert recover.arg_names == {"node_id"}
    assert (recover.side_effects, recover.timeout_s) == ("writes", 120)
    assert recover.description == "Diagnose a failed node and propose a fix."


def test_a_module_level_agent_tool_is_found_by_the_catalog():
    module = ModuleType("agent_system.toolpacks.control.plan")
    module.plan = PLAN  # type: ignore[attr-defined]

    assert spec_of(PLAN) is PLAN
    assert tools_in(module) == [PLAN]
    catalog = Catalog(tools=ToolRegistry([PLAN]), agents=AgentRegistry([COMPOSER]))
    assert catalog.problems() == []


# --- a call


def test_the_agent_runs_once_and_its_validated_output_is_returned():
    runtime = _runtime(composer=_proposal())
    token = CancelToken()

    result = _call(PLAN, {"request": "Does texture predict survival?"}, _ctx(runtime), token)

    assert result == {"summary": "Segment, then extract features.", "nodes": ["seg", "features"]}
    (call,) = runtime.engine.calls
    assert call.spec is COMPOSER
    assert call.task == PLAN.description
    assert dict(call.inputs) == {"request": "Does texture predict survival?"}
    assert (call.ctx.caller, call.ctx.subagent, call.ctx.depth) == ("main", "composer", 1)
    assert call.cancel is token


def test_output_not_matching_the_contract_is_an_error():
    runtime = _runtime(composer=AgentResult(status="completed", output={"summary": "no nodes"}))

    result = _call(PLAN, {"request": "x"}, _ctx(runtime))

    assert result == error_result(
        "invalid_output",
        "composer output does not match its contract: nodes: Field required",
        agent_type="composer",
    )


def test_a_failed_agent_is_an_error_with_its_code():
    failed = AgentResult(status="failed", error_code="max_iterations", summary="gave up")

    result = _call(PLAN, {"request": "x"}, _ctx(_runtime(composer=failed)))

    assert result == error_result("max_iterations", "gave up", agent_type="composer")


def test_the_hard_timeout_stops_the_agent():
    async def slow(spec, task, inputs, ctx, cancel) -> AgentResult:
        await asyncio.sleep(5)
        return _proposal()

    quick_plan = make_agent_tool("plan", "composer", GraphProposal, timeout_s=0.05)

    result = _call(quick_plan, {"request": "x"}, _ctx(_runtime(composer=slow)))

    assert result == error_result("timeout", "plan did not finish within 0.05 s")


def test_invalid_arguments_never_reach_the_agent():
    runtime = _runtime()

    result = _call(PLAN, {"question": "x"}, _ctx(runtime))

    assert result["error_code"] == "invalid_args"
    assert runtime.engine.calls == []


@pytest.mark.parametrize(
    ("agent_type", "code"),
    [("planner", "unknown_agent"), ("segmentation_agent", "not_a_control_agent")],
)
def test_only_a_known_control_agent_can_answer(agent_type, code):
    runtime = _runtime()
    spec = make_agent_tool("plan", agent_type, GraphProposal)

    assert _call(spec, {"request": "x"}, _ctx(runtime))["error_code"] == code
    assert runtime.engine.calls == []


def test_a_session_without_an_agent_runtime():
    assert _call(PLAN, {"request": "x"}, _ctx(None)) == error_result(
        "delegation_unavailable", "this session cannot start agents"
    )


# --- post


def test_post_receives_the_validated_output_and_shapes_the_result():
    seen: list[Any] = []

    async def post(output: BaseModel, ask: Any, ctx: ToolContext) -> dict[str, Any]:
        seen.append((type(output).__name__, ctx.tool_name))
        return {"proposal_id": "p-1", "errors": []}

    spec = make_agent_tool("plan", "composer", GraphProposal, post)
    result = _call(spec, {"request": "x"}, _ctx(_runtime(composer=_proposal())))

    assert result == {"proposal_id": "p-1", "errors": []}
    assert seen == [("GraphProposal", "plan")]


def test_post_can_ask_again_to_repair_the_output():
    tasks: list[str] = []

    async def composer(spec, task, inputs, ctx, cancel) -> AgentResult:
        tasks.append(task)
        return _proposal(nodes=["seg", "features"] if len(tasks) > 1 else ["seg"])

    async def validate_and_repair(output: Any, ask: Any, ctx: ToolContext) -> BaseModel:
        for _ in range(2):  # PLAN_REPAIR_ROUNDS
            if "features" in output.nodes:
                return output
            output = await ask("Validator: MISSING_INPUT — feature extraction is missing.")
        return output

    spec = make_agent_tool("plan", "composer", GraphProposal, validate_and_repair)
    result = _call(spec, {"request": "x"}, _ctx(_runtime(composer=composer)))

    assert result["nodes"] == ["seg", "features"]
    assert tasks == [
        spec.description,
        f"{spec.description}\n\nValidator: MISSING_INPUT — feature extraction is missing.",
    ]


def test_a_failure_while_repairing_is_reported():
    calls: list[int] = []

    async def composer(spec, task, inputs, ctx, cancel) -> AgentResult:
        calls.append(1)
        if len(calls) == 1:
            return _proposal()
        return AgentResult(status="completed", output={"summary": "broken"})

    async def always_repair(output: Any, ask: Any, ctx: ToolContext) -> BaseModel:
        return await ask("try again")

    spec = make_agent_tool("plan", "composer", GraphProposal, always_repair)
    result = _call(spec, {"request": "x"}, _ctx(_runtime(composer=composer)))

    assert result["error_code"] == "invalid_output"


# --- events


def test_the_agents_tool_calls_are_attributed_to_main_and_name_the_agent():
    class Sink:
        def __init__(self) -> None:
            self.events: list[ToolEvent] = []

        async def emit(self, event: ToolEvent) -> None:
            self.events.append(event)

        async def emit_view(self, event: ViewEvent) -> None: ...

    @tool(pack="control", side_effects="none", cost="light", execution="inline",
          visibility={"main"}, read_only=True)  # fmt: skip
    async def registry_search(query: str) -> dict[str, Any]:
        """Search the registry."""
        return {"hits": []}

    async def composer(spec, task, inputs, ctx, cancel) -> AgentResult:
        await Dispatcher().invoke(spec_of(registry_search), {"query": "segment"}, ctx, cancel)
        return _proposal()

    sink = Sink()
    _call(PLAN, {"request": "x"}, _ctx(_runtime(composer=composer), event_sink=sink))

    assert [(e.tool_name, e.event_type, e.source, e.subagent) for e in sink.events] == [
        ("plan", "tool_started", "main_agent", None),
        ("registry_search", "tool_started", "main_agent", "composer"),
        ("registry_search", "tool_finished", "main_agent", "composer"),
        ("plan", "tool_finished", "main_agent", None),
    ]
