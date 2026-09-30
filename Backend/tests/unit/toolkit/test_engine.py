"""The agent engine contract and FakeAgentEngine (merged plan, Phase 1.4 step 3)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_system.toolkit.agents.engine import (
    AgentEngine,
    AgentResult,
    AgentStatus,
    FakeAgentEngine,
    Usage,
)
from agent_system.toolkit.agents.spec import AgentSpec
from agent_system.toolkit.context import ToolContext
from agent_system.toolkit.runtime.cancel import CancelToken

pytestmark = pytest.mark.unit

CTX = ToolContext(session_id="s1", caller="agent", agent_type="segmentation_agent")
AGENT = AgentSpec(
    agent_type="mask_resolution_agent",
    description="Resolves masks.",
    kind="internal",
    parent="segmentation_agent",
    system_prompt="Resolve.",
)


def test_results():
    done = AgentResult(status="completed", output={"model": "vista3d"}, summary="picked vista3d")

    assert done.status is AgentStatus.COMPLETED
    assert done.usage == Usage()
    assert AgentResult(status="failed", error_code="max_iterations").error_code == "max_iterations"


@pytest.mark.parametrize(
    ("fields", "match"),
    [
        ({"status": "completed", "error_code": "error"}, "no error_code"),
        ({"status": "failed"}, "needs an error_code"),
        ({"status": "cancelled", "error_code": ""}, "needs an error_code"),
        ({"status": "paused"}, "status='paused'"),
    ],
)
def test_inconsistent_results_are_refused(fields, match):
    with pytest.raises(ValueError, match=match):
        AgentResult(**fields)


def test_usage_adds_up():
    total = Usage(100, 20) + Usage(50, 5)

    assert (total.prompt_tokens, total.completion_tokens, total.total_tokens) == (150, 25, 175)


def test_fake_engine_is_an_engine():
    assert isinstance(FakeAgentEngine(), AgentEngine)


def test_an_unscripted_agent_completes():
    result = asyncio.run(FakeAgentEngine().run(AGENT, "Resolve liver.", {}, CTX))

    assert (result.status, result.summary) == (AgentStatus.COMPLETED, "mask_resolution_agent done")


def test_a_scripted_result_is_returned():
    failed = AgentResult(status="failed", error_code="error", summary="no model fits")
    engine = FakeAgentEngine({"mask_resolution_agent": failed})

    assert asyncio.run(engine.run(AGENT, "Resolve liver.", {}, CTX)) is failed


def test_a_scripted_function_gets_the_run_arguments_and_calls_are_recorded():
    seen: list[Any] = []

    async def script(spec, task, inputs, ctx, cancel) -> AgentResult:
        seen.append((spec.name, task, dict(inputs), ctx.session_id, cancel))
        return AgentResult(status="completed", output={"organ": inputs["organ"]})

    engine = FakeAgentEngine({"mask_resolution_agent": script})
    token = CancelToken()

    result = asyncio.run(engine.run(AGENT, "Resolve.", {"organ": "liver"}, CTX, token))

    assert result.output == {"organ": "liver"}
    assert seen == [("mask_resolution_agent", "Resolve.", {"organ": "liver"}, "s1", token)]
    assert [(call.spec, call.task, call.cancel) for call in engine.calls] == [
        (AGENT, "Resolve.", token)
    ]
