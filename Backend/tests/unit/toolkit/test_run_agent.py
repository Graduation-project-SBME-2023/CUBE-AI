"""run_agent and the delegation core (merged plan, Phase 1.4 step 4)."""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest
from pydantic import BaseModel

from agent_system.toolkit.agents.engine import AgentResult
from agent_system.toolkit.agents.registry import AgentRegistry
from agent_system.toolkit.agents.spec import AgentSpec
from agent_system.toolkit.catalog import PACK_PACKAGES, build
from agent_system.toolkit.context import ToolContext
from agent_system.toolkit.delegation.run_agent import delegate, max_agent_depth, run_agent
from agent_system.toolkit.results import error_result
from agent_system.toolkit.runtime.cancel import CancelToken
from agent_system.toolkit.runtime.dispatch import Dispatcher
from agent_system.toolkit.tools.registry import ToolRegistry, spec_of, tool
from tests.support.toolkit_fixtures import FakeAgentRuntime, FakeEventSink, fake_runtime

pytestmark = pytest.mark.unit

RUN_AGENT = spec_of(run_agent)


class MaskChoice(BaseModel):
    model: str
    labels: list[str]


def _agent(name: str, kind: str, **fields: Any) -> AgentSpec:
    return AgentSpec(
        agent_type=name, description=f"{name}.", kind=kind, system_prompt="Prompt.", **fields
    )


SEGMENTATION = _agent(
    "segmentation_agent",
    "execution",
    allowed_tools=["run_agent"],
    delegates=["mask_resolution_agent", "clinical_target_intent_agent"],
)
MASK = _agent(
    "mask_resolution_agent",
    "internal",
    parent="segmentation_agent",
    output_contract=MaskChoice,
    allowed_tools=["run_agent"],
    delegates=["label_checker_agent"],
)
INTENT = _agent("clinical_target_intent_agent", "internal", parent="segmentation_agent")
CHECKER = _agent(
    "label_checker_agent",
    "internal",
    parent="mask_resolution_agent",
    delegates=["label_checker_agent"],  # recursion, so the depth cap is what stops it
)
LITERATURE = _agent("literature_review_agent", "execution")


def _runtime(**scripts: Any) -> FakeAgentRuntime:
    return fake_runtime([SEGMENTATION, MASK, INTENT, CHECKER, LITERATURE], **scripts)


def _ctx(runtime: Any, **fields: Any) -> ToolContext:
    return ToolContext(
        session_id="s1",
        caller="agent",
        agent_type="segmentation_agent",
        session_handle=runtime,
        **fields,
    )


def _call(ctx: ToolContext, args: dict[str, Any], cancel: CancelToken | None = None) -> Any:
    return asyncio.run(Dispatcher().invoke(RUN_AGENT, args, ctx, cancel))


def _completed(output: Any = None, summary: str = "done") -> AgentResult:
    return AgentResult(status="completed", output=output, summary=summary)


# --- the tool


def test_run_agent_is_discovered_in_the_delegation_pack():
    catalog = build(packages=[PACK_PACKAGES[0]], entry_point_groups=())

    assert catalog.tools.get("run_agent") is RUN_AGENT
    assert RUN_AGENT.pack == "delegation"
    assert RUN_AGENT.visibility == {"agent"}
    catalog.validate()


def test_the_enum_holds_only_the_callers_delegates():
    registry = ToolRegistry([RUN_AGENT])
    ctx = _ctx(None)

    (for_segmentation,) = registry.for_agent(SEGMENTATION, ctx)
    (for_mask,) = registry.for_agent(MASK, ctx)

    assert for_segmentation.args_schema()["properties"]["agent_type"]["enum"] == [
        "mask_resolution_agent",
        "clinical_target_intent_agent",
    ]
    assert for_mask.args_schema()["properties"]["agent_type"]["enum"] == ["label_checker_agent"]
    assert for_mask.args_schema()["properties"]["agent_type"]["description"] == (
        "The internal agent to run."
    )
    assert RUN_AGENT.args_schema()["properties"]["agent_type"]["type"] == "string"  # unbound


def test_the_bound_schema_refuses_other_agents_before_the_call():
    (bound,) = ToolRegistry([RUN_AGENT]).for_agent(SEGMENTATION, _ctx(None))
    runtime = _runtime()

    result = asyncio.run(
        Dispatcher().invoke(bound, {"agent_type": "pico_analyst", "task": "x"}, _ctx(runtime))
    )

    assert result["error_code"] == "invalid_args"
    assert runtime.engine.calls == []


# --- a delegated run


def test_a_delegate_runs_and_its_output_is_validated():
    runtime = _runtime(mask_resolution_agent=_completed({"model": "vista3d", "labels": ["liver"]}))

    args = {
        "agent_type": "mask_resolution_agent",
        "task": "Pick a model.",
        "inputs": {"organ": "liver"},
    }

    result = _call(_ctx(runtime), args)

    assert result == {
        "status": "completed",
        "agent_type": "mask_resolution_agent",
        "summary": "done",
        "output": {"model": "vista3d", "labels": ["liver"]},
    }
    (call,) = runtime.engine.calls
    assert (call.spec, call.task, dict(call.inputs)) == (MASK, "Pick a model.", {"organ": "liver"})
    assert (call.ctx.subagent, call.ctx.depth, call.ctx.agent_type) == (
        "mask_resolution_agent",
        1,
        "segmentation_agent",
    )


def test_output_not_matching_the_contract_is_an_error():
    runtime = _runtime(mask_resolution_agent=_completed({"model": "vista3d"}))

    result = _call(_ctx(runtime), {"agent_type": "mask_resolution_agent", "task": "Pick."})

    assert result == error_result(
        "invalid_output",
        "mask_resolution_agent output does not match its contract: labels: Field required",
        agent_type="mask_resolution_agent",
    )


def test_output_without_a_contract_passes_through():
    runtime = _runtime(clinical_target_intent_agent=_completed("liver lesions"))

    result = _call(_ctx(runtime), {"agent_type": "clinical_target_intent_agent", "task": "Why?"})

    assert result["output"] == "liver lesions"


def test_a_failed_child_is_an_error_with_its_code():
    failed = AgentResult(status="failed", error_code="max_iterations", summary="ran out of steps")
    runtime = _runtime(mask_resolution_agent=failed)

    result = _call(_ctx(runtime), {"agent_type": "mask_resolution_agent", "task": "Pick."})

    assert result == error_result(
        "max_iterations", "ran out of steps", agent_type="mask_resolution_agent"
    )


def test_a_child_over_its_timeout_is_stopped():
    async def slow(spec, task, inputs, ctx, cancel) -> AgentResult:
        await asyncio.sleep(5)
        return _completed()

    runtime = _runtime(clinical_target_intent_agent=slow)
    runtime.agents = AgentRegistry(
        [SEGMENTATION, MASK, copy.replace(INTENT, timeout_s=0.05), CHECKER, LITERATURE]
    )

    result = _call(_ctx(runtime), {"agent_type": "clinical_target_intent_agent", "task": "Why?"})

    assert result["error_code"] == "timeout"
    assert result["agent_type"] == "clinical_target_intent_agent"


# --- who may start whom


@pytest.mark.parametrize(
    ("agent_type", "message"),
    [
        ("label_checker_agent", "segmentation_agent may not start 'label_checker_agent'"),
        ("literature_review_agent", "segmentation_agent may not start 'literature_review_agent'"),
        ("pico_analyst", "segmentation_agent may not start 'pico_analyst'"),
    ],
)
def test_an_agent_may_start_only_its_own_delegates(agent_type, message):
    runtime = _runtime()

    result = _call(_ctx(runtime), {"agent_type": agent_type, "task": "x"})

    assert result["error_code"] == "not_a_delegate"
    assert result["error_message"].startswith(message)
    assert runtime.engine.calls == []


def test_a_delegate_must_be_internal():
    runtime = _runtime()
    runtime.agents = AgentRegistry(
        [copy.replace(SEGMENTATION, delegates=["literature_review_agent"]), LITERATURE]
    )

    result = _call(_ctx(runtime), {"agent_type": "literature_review_agent", "task": "x"})

    assert result == error_result(
        "not_a_delegate", "'literature_review_agent' is not an internal agent"
    )


def test_only_an_agent_can_delegate():
    runtime = _runtime()
    main_ctx = ToolContext(session_id="s1", caller="main", session_handle=runtime)

    assert _call(main_ctx, {"agent_type": "mask_resolution_agent", "task": "x"})["error_code"] == (
        "not_a_delegate"
    )


def test_a_session_without_an_agent_runtime():
    result = _call(_ctx(None), {"agent_type": "mask_resolution_agent", "task": "x"})

    assert result == error_result("delegation_unavailable", "this session cannot start agents")


# --- nesting and depth


def _nested_runtime(outcomes: list[Any]) -> FakeAgentRuntime:
    """Each internal agent calls run_agent on its first delegate, the way a
    model would, and records what that call returned."""

    async def delegate_further(spec, task, inputs, ctx, cancel) -> AgentResult:
        args = {"agent_type": spec.delegates[0], "task": "go deeper"}
        outcomes.append(await Dispatcher().invoke(RUN_AGENT, args, ctx, cancel))
        return _completed({"model": "m", "labels": []})

    return _runtime(mask_resolution_agent=delegate_further, label_checker_agent=delegate_further)


def test_the_depth_is_capped():
    outcomes: list[Any] = []
    runtime = _nested_runtime(outcomes)

    _call(_ctx(runtime), {"agent_type": "mask_resolution_agent", "task": "start"})

    # segmentation (0) -> mask (1) -> label_checker (2) -> label_checker (3) is refused
    assert [call.ctx.depth for call in runtime.engine.calls] == [1, 2]
    assert [call.spec.name for call in runtime.engine.calls] == [
        "mask_resolution_agent",
        "label_checker_agent",
    ]
    assert outcomes[0] == error_result("max_depth", "internal agents may nest at most 2 deep")
    assert outcomes[1]["status"] == "completed"  # mask's own call to label_checker


def test_the_depth_cap_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("MAX_AGENT_DEPTH", "3")
    outcomes: list[Any] = []
    runtime = _nested_runtime(outcomes)

    _call(_ctx(runtime), {"agent_type": "mask_resolution_agent", "task": "start"})

    assert [call.ctx.depth for call in runtime.engine.calls] == [1, 2, 3]


@pytest.mark.parametrize(("raw", "depth"), [("", 2), ("abc", 2), ("0", 2), ("-1", 2), ("4", 4)])
def test_max_agent_depth_falls_back_on_bad_values(monkeypatch, raw, depth):
    monkeypatch.setenv("MAX_AGENT_DEPTH", raw)

    assert max_agent_depth() == depth


# --- cancel and events


def test_the_cancel_token_is_passed_down():
    seen: list[Any] = []

    async def script(spec, task, inputs, ctx, cancel) -> AgentResult:
        seen.append((cancel, ctx.cancel))
        return _completed({"model": "m", "labels": []})

    token = CancelToken()
    runtime = _runtime(mask_resolution_agent=script)
    _call(_ctx(runtime), {"agent_type": "mask_resolution_agent", "task": "x"}, token)

    assert seen == [(token, token)]


def test_cancelling_the_node_stops_the_child():
    stopped: list[bool] = []

    async def long_child(spec, task, inputs, ctx, cancel) -> AgentResult:
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            stopped.append(True)
            raise
        return _completed()

    async def scenario() -> Any:
        token = CancelToken()
        runtime = _runtime(clinical_target_intent_agent=long_child)
        args = {"agent_type": "clinical_target_intent_agent", "task": "x"}
        call = asyncio.create_task(Dispatcher().invoke(RUN_AGENT, args, _ctx(runtime), token))
        await asyncio.sleep(0.01)
        token.cancel()
        return await call

    assert asyncio.run(scenario()) == error_result("cancelled", "run_agent was cancelled")
    assert stopped == [True]


def test_the_childs_tool_events_keep_the_node_source_and_name_the_subagent():
    sink = FakeEventSink()

    @tool(pack="segmentation", side_effects="none", cost="light", execution="inline",
          visibility={"agent"}, read_only=True)  # fmt: skip
    async def list_models(organ: str) -> dict[str, Any]:
        """List models."""
        return {"models": ["vista3d"]}

    async def script(spec, task, inputs, ctx, cancel) -> AgentResult:
        await Dispatcher().invoke(spec_of(list_models), {"organ": "liver"}, ctx, cancel)
        return _completed({"model": "vista3d", "labels": []})

    runtime = _runtime(mask_resolution_agent=script)
    _call(_ctx(runtime, event_sink=sink), {"agent_type": "mask_resolution_agent", "task": "x"})

    assert [(e.tool_name, e.event_type, e.source, e.subagent) for e in sink.events] == [
        ("run_agent", "tool_started", "segmentation_agent", None),
        ("list_models", "tool_started", "segmentation_agent", "mask_resolution_agent"),
        ("list_models", "tool_finished", "segmentation_agent", "mask_resolution_agent"),
        ("run_agent", "tool_finished", "segmentation_agent", None),
    ]


# --- the core, called directly (the segmentation module, Plan 9.4)


def test_delegate_can_be_called_without_a_tool_call():
    runtime = _runtime(clinical_target_intent_agent=_completed("liver"))

    result = asyncio.run(
        delegate(runtime, "clinical_target_intent_agent", "Why?", {}, _ctx(runtime))
    )

    assert (result["status"], result["output"]) == ("completed", "liver")
