"""The agent engine contract and FakeAgentEngine (merged plan, Phase 1.4 step 3; Phase 9.3)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_system.toolkit.agents.engine import (
    AgentEngine,
    AgentResult,
    AgentStatus,
    EngineHooks,
    FakeAgentEngine,
    MainSession,
    Resume,
    Suspension,
    SuspensionKind,
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
MAIN = AgentSpec(
    agent_type="main_agent",
    description="Talks to the user.",
    kind="control",
    system_prompt="Help.",
)
MAIN_CTX = ToolContext(session_id="s1", caller="main", agent_type="main_agent")
ASK = Suspension(kind="ask_user", tool_use_id="toolu_1", payload={"form": "cohort_approval"})


def test_results():
    done = AgentResult(status="completed", output={"model": "vista3d"}, summary="picked vista3d")

    assert done.status is AgentStatus.COMPLETED
    assert done.usage == Usage()
    assert AgentResult(status="failed", error_code="max_iterations").error_code == "max_iterations"


def test_a_suspended_result_says_what_it_waits_for():
    result = AgentResult(status="suspended", suspension=ASK)

    assert result.status is AgentStatus.SUSPENDED
    assert result.suspension.kind is SuspensionKind.ASK_USER
    assert result.error_code is None


@pytest.mark.parametrize(
    ("fields", "match"),
    [
        ({"status": "completed", "error_code": "error"}, "no error_code"),
        ({"status": "failed"}, "needs an error_code"),
        ({"status": "cancelled", "error_code": ""}, "needs an error_code"),
        ({"status": "paused"}, "status='paused'"),
        ({"status": "suspended"}, "needs a suspension"),
        ({"status": "suspended", "suspension": ASK, "error_code": "error"}, "no error_code"),
        ({"status": "completed", "suspension": ASK}, "no suspension"),
        ({"status": "failed", "error_code": "error", "suspension": ASK}, "no suspension"),
    ],
)
def test_inconsistent_results_are_refused(fields, match):
    with pytest.raises(ValueError, match=match):
        AgentResult(**fields)


@pytest.mark.parametrize(
    ("fields", "match"),
    [
        ({"kind": "ask_user"}, "needs the deferred call's tool_use_id"),
        ({"kind": "tool_approval", "tool_use_id": ""}, "needs the deferred call's tool_use_id"),
        ({"kind": "pause", "tool_use_id": "toolu_1"}, "a pause has no deferred call"),
        ({"kind": "question"}, "kind='question'"),
    ],
)
def test_inconsistent_suspensions_are_refused(fields, match):
    with pytest.raises(ValueError, match=match):
        Suspension(**fields)


def test_a_pause_needs_no_deferred_call():
    assert Suspension(kind="pause").tool_use_id is None


def test_resume_answers_a_deferred_call_or_just_continues():
    answer = Resume({"decision": "approved"})

    assert (answer.value, answer.answers_call) == ({"decision": "approved"}, True)
    assert Resume.continue_().answers_call is False
    with pytest.raises(ValueError, match="carries no value"):
        Resume("yes", answers_call=False)


def test_engine_hooks_hold_the_pause_flag_and_the_mailbox():
    hooks = EngineHooks()

    assert not hooks.pause.is_set()
    hooks.mailbox.put_nowait("also count meningioma")
    assert hooks.mailbox.get_nowait() == "also count meningioma"


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
    assert [(call.spec, call.task, call.cancel, call.resume) for call in engine.calls] == [
        (AGENT, "Resolve.", token, None)
    ]


def test_a_scripted_suspension_then_the_resumed_run():
    done = AgentResult(status="completed", output={"approved": True})
    engine = FakeAgentEngine({"mask_resolution_agent": [AgentResult(status="suspended", suspension=ASK), done]})
    answer = Resume({"decision": "approved"})

    async def scenario() -> tuple[AgentResult, AgentResult]:
        first = await engine.run(AGENT, "Resolve.", {}, CTX)
        second = await engine.run(AGENT, "Resolve.", {}, CTX, resume=answer)
        return first, second

    first, second = asyncio.run(scenario())

    assert (first.status, first.suspension) == (AgentStatus.SUSPENDED, ASK)
    assert second is done
    assert [call.resume for call in engine.calls] == [None, answer]


def test_a_scripted_function_that_declares_resume_gets_it():
    async def script(spec, task, inputs, ctx, cancel, *, resume: Resume | None = None) -> AgentResult:
        if resume is None:
            return AgentResult(status="suspended", suspension=ASK)
        return AgentResult(status="completed", output=resume.value)

    engine = FakeAgentEngine({"mask_resolution_agent": script})

    async def scenario() -> tuple[AgentResult, AgentResult]:
        first = await engine.run(AGENT, "Resolve.", {}, CTX)
        return first, await engine.run(AGENT, "Resolve.", {}, CTX, resume=Resume({"ok": True}))

    first, second = asyncio.run(scenario())

    assert first.status is AgentStatus.SUSPENDED
    assert second.output == {"ok": True}


def test_a_sequence_script_that_runs_out_says_so():
    engine = FakeAgentEngine({"mask_resolution_agent": [AgentResult(status="completed")]})

    async def scenario() -> None:
        await engine.run(AGENT, "Resolve.", {}, CTX)
        await engine.run(AGENT, "Resolve.", {}, CTX)

    with pytest.raises(RuntimeError, match="no result left"):
        asyncio.run(scenario())


def test_the_main_session_opens_then_continues_on_the_next_open():
    engine = FakeAgentEngine()

    async def scenario() -> tuple[MainSession, MainSession]:
        first = await engine.open_main(MAIN, MAIN_CTX)
        await first.close()
        return first, await engine.open_main(MAIN, MAIN_CTX)

    first, second = asyncio.run(scenario())

    assert isinstance(first, MainSession)
    assert (first.sdk_session_id, first.resumed, first.closed) == ("s1", False, True)
    assert (second.sdk_session_id, second.resumed) == ("s1", True)


def test_main_turns_are_scripted_and_recorded():
    async def reply(session, message, cancel) -> AgentResult:
        return AgentResult(status="completed", summary=f"you said: {message}")

    engine = FakeAgentEngine(turns={"main_agent": reply})

    async def scenario() -> AgentResult:
        session = await engine.open_main(MAIN, MAIN_CTX)
        return await engine.turn(session, "count glioma patients")

    result = asyncio.run(scenario())

    assert result.summary == "you said: count glioma patients"
    assert [(call.session.sdk_session_id, call.message) for call in engine.turn_calls] == [
        ("s1", "count glioma patients")
    ]


def test_an_unscripted_turn_replies():
    engine = FakeAgentEngine()

    async def scenario() -> AgentResult:
        return await engine.turn(await engine.open_main(MAIN, MAIN_CTX), "hi")

    assert asyncio.run(scenario()).summary == "main_agent replied"
