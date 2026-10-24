"""NodeRunner tests, with InMemoryGraphStore, FakeAgentEngine and inline tools."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from pydantic import BaseModel

from agent_system.execution.in_memory_graph_store import InMemoryGraphStore
from agent_system.execution.node_runner import NodeRunner
from agent_system.toolkit.agents.engine import (
    AgentResult,
    AgentStatus,
    FakeAgentEngine,
    Resume,
    Suspension,
    SuspensionKind,
    Usage,
)
from agent_system.toolkit.agents.spec import AgentSpec
from agent_system.toolkit.context import ToolContext
from agent_system.toolkit.ports import ExtractedArtifact, PortSpec
from agent_system.toolkit.results import error_result
from agent_system.toolkit.runtime.cancel import CancelToken
from agent_system.toolkit.tools.spec import ToolSpec
from tests.support.graph_store_scenarios import node, submit
from tests.support.toolkit_fixtures import make_catalog

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

COHORT_ID = str(uuid.uuid4())
FEATURE_SET_ID = str(uuid.uuid4())


# ----------------------------------------------------------------------
# A small catalog: an agent that builds a cohort, a tool that reads it
# ----------------------------------------------------------------------
def _cohort(result: Any) -> ExtractedArtifact:
    return ExtractedArtifact(ref={"cohort_id": result.output["cohort_id"]},
                             summary={"size": result.output["size"], "frozen": True})


def _features(result: Any) -> ExtractedArtifact:
    return ExtractedArtifact(ref={"feature_set_id": result["feature_set_id"]},
                             summary={"n_rows": result["n_rows"], "n_features": 4})


class FeatureArgs(BaseModel):
    cohort_id: str
    bin_width: float = 25.0


class Calls:
    """What the tools saw, per test."""

    def __init__(self) -> None:
        self.args: list[dict[str, Any]] = []
        self.contexts: list[ToolContext] = []
        self.started = asyncio.Event()


def _tool(name: str, handler: Any, **fields: Any) -> ToolSpec:
    manifest: dict[str, Any] = {"side_effects": "writes", "cost": "light", "execution": "inline",
                                "visibility": {"graph"}}
    manifest.update(fields)
    return ToolSpec(name=name, description=f"{name}.", pack="research", handler=handler,
                    input_model=FeatureArgs, context_param="ctx", **manifest)


def catalog(calls: Calls, *, on_complete: Any = None):
    async def extract(ctx: ToolContext, **args: Any) -> dict[str, Any]:
        calls.args.append(args)
        calls.contexts.append(ctx)
        return {"feature_set_id": FEATURE_SET_ID, "n_rows": 12}

    async def broken(ctx: ToolContext, **args: Any) -> dict[str, Any]:
        return error_result("no_masks", "the cohort has no masks")

    async def slow(ctx: ToolContext, **args: Any) -> dict[str, Any]:
        calls.contexts.append(ctx)
        calls.started.set()
        await asyncio.Event().wait()  # runs until cancelled
        return {}

    async def wrong(ctx: ToolContext, **args: Any) -> dict[str, Any]:
        return {"feature_set_id": "not-a-uuid", "n_rows": 1}

    reads = {"cohort": PortSpec("Cohort", param="cohort_id")}
    features = {"features": PortSpec("FeatureTable", extract=_features)}
    return make_catalog(
        AgentSpec(agent_type="cohort_agent", description="Builds a cohort.", kind="execution",
                  system_prompt="Work.", outputs={"cohort": PortSpec("Cohort", extract=_cohort)},
                  on_complete=on_complete),
        AgentSpec(agent_type="study_population_planner_agent", description="Builds a cohort, shown in a view.",
                  kind="execution", system_prompt="Work.",
                  outputs={"cohort": PortSpec("Cohort", extract=_cohort)}),
        AgentSpec(agent_type="stateless_agent", description="Builds a cohort, keeping no session.",
                  kind="execution", system_prompt="Work.", resumable=False,
                  outputs={"cohort": PortSpec("Cohort", extract=_cohort)}),
        AgentSpec(agent_type="reader_agent", description="Reads a cohort.", kind="execution",
                  system_prompt="Read.", inputs={"cohort": PortSpec("Cohort")}),
        _tool("extract_features", extract, inputs=reads, outputs=features),
        _tool("broken_features", broken, inputs=reads, outputs=features),
        _tool("slow_features", slow, inputs=reads, outputs=features),
        _tool("wrong_features", wrong, inputs=reads, outputs=features),
        _tool("gpu_features", extract, inputs=reads, outputs=features, cost="heavy", execution="job",
              resources={"gpu"}),
    )


def cohort_done(size: int = 12) -> AgentResult:
    return AgentResult(status=AgentStatus.COMPLETED, output={"cohort_id": COHORT_ID, "size": size})


async def graph(store: InMemoryGraphStore, *refs: str) -> tuple[str, list[dict[str, Any]]]:
    session_id = await store.create_session("q")
    nodes = [node(ref, "agent" if ref.endswith("_agent") else "tool",
                  params={"task": "build it"} if ref.endswith("_agent") else {}) for ref in refs]
    await submit(store, session_id, nodes)
    return session_id, nodes


@pytest.fixture
def calls() -> Calls:
    return Calls()


@pytest.fixture
def store() -> InMemoryGraphStore:
    return InMemoryGraphStore()


def runner(store, calls, **scripts: Any) -> tuple[NodeRunner, FakeAgentEngine]:
    engine = FakeAgentEngine({"cohort_agent": cohort_done(), **scripts})
    return NodeRunner(store, catalog(calls), engine=engine), engine


async def run(runner: NodeRunner, store, session_id: str, node_id: str, **options: Any):
    return await runner.run(await store.get_graph(session_id), node_id, **options)


async def current_run(store, session_id: str, node_id: str):
    graph_view = await store.get_graph(session_id)
    return next(item.run for item in graph_view.nodes if item.node.node_id == node_id)


# ----------------------------------------------------------------------
# Agent and tool nodes that complete
# ----------------------------------------------------------------------
async def test_an_agent_node_runs_and_its_output_is_stored_as_a_table_ref(store, calls):
    node_runner, engine = runner(store, calls)
    session_id, [agent] = await graph(store, "cohort_agent")

    report = await run(node_runner, store, session_id, agent["node_id"])

    assert report.status == "completed" and report.code is None
    stored = await current_run(store, session_id, agent["node_id"])
    assert stored.status == "completed" and stored.attempts[0].state == "succeeded"
    assert stored.outputs["ports"]["cohort"] == {
        "type": "Cohort", "ref": {"table": "cohorts", "ids": [COHORT_ID]},
        "summary": {"size": 12, "label_counts": {}, "frozen": True}, "phi": True,
    }
    assert stored.execution_policy["resumable"] is True and stored.execution_policy["spec_digest"]
    [call] = engine.calls
    assert call.task == "build it"
    # The agent's writes carry its attempt, so the store can refuse those of a stale attempt.
    assert (call.ctx.run_id, call.ctx.attempt_id) == (report.run_id, report.attempt_id)
    assert call.ctx.caller == "agent" and call.ctx.agent_type == "cohort_agent"


async def test_a_tool_node_receives_its_pinned_input_in_the_param(store, calls):
    node_runner, _ = runner(store, calls)
    session_id, [agent, tool] = await graph(store, "cohort_agent", "extract_features")
    first = await run(node_runner, store, session_id, agent["node_id"])

    report = await run(node_runner, store, session_id, tool["node_id"])

    assert report.status == "completed"
    assert calls.args == [{"cohort_id": COHORT_ID, "bin_width": 25.0}]
    assert calls.contexts[0].caller == "node" and calls.contexts[0].attempt_id == report.attempt_id
    stored = await current_run(store, session_id, tool["node_id"])
    assert stored.resolved_inputs["ports"]["cohort"] == {
        "node_id": agent["node_id"], "run_id": first.run_id, "port": "cohort", "type": "Cohort",
        "ref": {"table": "cohorts", "ids": [COHORT_ID]},
    }
    assert stored.outputs["ports"]["features"]["ref"] == {"table": "feature_sets", "ids": [FEATURE_SET_ID]}


async def test_an_agent_reads_its_inputs_in_the_node_inputs_section(store, calls):
    node_runner, engine = runner(store, calls)
    session_id, [agent, reader] = await graph(store, "cohort_agent", "reader_agent")
    await run(node_runner, store, session_id, agent["node_id"])

    report = await run(node_runner, store, session_id, reader["node_id"])

    assert report.status == "completed"
    call = engine.calls[-1]
    assert "## Node inputs" in call.task and COHORT_ID in call.task
    assert call.inputs["cohort"]["refs"] == [{"cohort_id": COHORT_ID}]
    assert call.inputs["cohort"]["summary"]["size"] == 12


async def test_on_complete_turns_the_agent_output_into_the_runs_output(store, calls):
    async def record(ctx: ToolContext, output: Any) -> dict[str, Any]:
        return {**output, "size": 30}

    engine = FakeAgentEngine({"cohort_agent": cohort_done()})
    node_runner = NodeRunner(store, catalog(calls, on_complete=record), engine=engine)
    session_id, [agent] = await graph(store, "cohort_agent")

    await run(node_runner, store, session_id, agent["node_id"])

    stored = await current_run(store, session_id, agent["node_id"])
    assert stored.outputs["ports"]["cohort"]["summary"]["size"] == 30


# ----------------------------------------------------------------------
# Nodes that cannot start
# ----------------------------------------------------------------------
async def test_a_node_whose_input_has_not_completed_does_not_start(store, calls):
    node_runner, _ = runner(store, calls)
    session_id, [_, tool] = await graph(store, "cohort_agent", "extract_features")

    report = await run(node_runner, store, session_id, tool["node_id"])

    assert (report.status, report.code, report.run_id) == ("not_started", "NOT_READY", None)
    assert await current_run(store, session_id, tool["node_id"]) is None


async def test_a_refusal_of_t4_starts_nothing(store, calls):
    node_runner, _ = runner(store, calls)
    session_id, [agent] = await graph(store, "cohort_agent")
    await store.set_control_state(session_id, "paused")

    report = await run(node_runner, store, session_id, agent["node_id"])

    assert (report.status, report.code) == ("not_started", "SESSION_NOT_ACTIVE")


async def test_a_job_tool_is_not_started_while_jobs_cannot_run(store, calls):
    node_runner, _ = runner(store, calls)
    session_id, [agent, tool] = await graph(store, "cohort_agent", "gpu_features")
    await run(node_runner, store, session_id, agent["node_id"])

    report = await run(node_runner, store, session_id, tool["node_id"])

    assert (report.status, report.code) == ("not_started", "execution_unavailable")


async def test_an_unknown_target_is_not_started(store, calls):
    node_runner, _ = runner(store, calls)
    session_id = await store.create_session("q")
    unknown = node("no_such_tool")
    await submit(store, session_id, [unknown])

    report = await run(node_runner, store, session_id, unknown["node_id"])

    assert (report.status, report.code) == ("not_started", "UNKNOWN_REF")


# ----------------------------------------------------------------------
# Failures
# ----------------------------------------------------------------------
async def test_an_agent_failure_fails_the_attempt_with_its_code(store, calls):
    failed = AgentResult(status=AgentStatus.FAILED, error_code="max_iterations", summary="ran out of turns")
    node_runner, _ = runner(store, calls, cohort_agent=failed)
    session_id, [agent] = await graph(store, "cohort_agent")

    report = await run(node_runner, store, session_id, agent["node_id"])

    assert (report.status, report.code) == ("failed", "max_iterations")
    stored = await current_run(store, session_id, agent["node_id"])
    assert stored.status == "failed" and stored.attempts[0].error["code"] == "max_iterations"


async def test_a_tool_error_result_fails_the_attempt_with_its_code(store, calls):
    node_runner, _ = runner(store, calls)
    session_id, [agent, tool] = await graph(store, "cohort_agent", "broken_features")
    await run(node_runner, store, session_id, agent["node_id"])

    report = await run(node_runner, store, session_id, tool["node_id"])

    assert (report.status, report.code) == ("failed", "no_masks")


async def test_an_output_that_breaks_its_artifact_type_is_bad_output(store, calls):
    node_runner, _ = runner(store, calls)
    session_id, [agent, tool] = await graph(store, "cohort_agent", "wrong_features")
    await run(node_runner, store, session_id, agent["node_id"])

    report = await run(node_runner, store, session_id, tool["node_id"])

    assert (report.status, report.code) == ("failed", "bad_output")
    assert (await current_run(store, session_id, tool["node_id"])).outputs is None


async def test_an_exception_in_the_work_fails_the_attempt(store, calls):
    async def explode(*_: Any) -> AgentResult:
        raise RuntimeError("engine down")

    node_runner, _ = runner(store, calls, cohort_agent=explode)
    session_id, [agent] = await graph(store, "cohort_agent")

    report = await run(node_runner, store, session_id, agent["node_id"])

    assert (report.status, report.code, report.detail) == ("failed", "error", "engine down")


# ----------------------------------------------------------------------
# Cancel
# ----------------------------------------------------------------------
async def test_a_cancel_stops_the_work_and_is_confirmed(store, calls):
    node_runner, _ = runner(store, calls)
    session_id, [agent, tool] = await graph(store, "cohort_agent", "slow_features")
    await run(node_runner, store, session_id, agent["node_id"])
    cancel = CancelToken()

    task = asyncio.create_task(run(node_runner, store, session_id, tool["node_id"], cancel=cancel))
    await asyncio.wait_for(calls.started.wait(), 1)
    ctx = calls.contexts[-1]
    await store.request_cancel(ctx.run_id, ctx.attempt_id)  # request the cancel, then fire the token
    cancel.cancel()
    report = await asyncio.wait_for(task, 1)

    assert (report.status, report.run_id) == ("cancelled", ctx.run_id)
    stored = await current_run(store, session_id, tool["node_id"])
    assert stored.status == "cancelled" and stored.attempts[0].state == "cancelled"


async def test_a_cancel_the_store_was_not_told_about_is_requested_then_confirmed(store, calls):
    async def stops(spec, task, inputs, ctx, cancel) -> AgentResult:
        cancel.cancel()  # e.g. the process is shutting down
        return AgentResult(status=AgentStatus.CANCELLED, error_code="cancelled")

    node_runner, _ = runner(store, calls, cohort_agent=stops)
    session_id, [agent] = await graph(store, "cohort_agent")

    report = await run(node_runner, store, session_id, agent["node_id"])

    assert report.status == "cancelled"
    assert (await current_run(store, session_id, agent["node_id"])).attempts[0].state == "cancelled"


async def test_a_late_result_of_a_superseded_attempt_is_not_recorded(store, calls):
    async def superseded_meanwhile(spec, task, inputs, ctx, cancel) -> AgentResult:
        # A rerun was applied while the agent worked: its attempt is cancelled and confirmed.
        await store.request_cancel(ctx.run_id, ctx.attempt_id)
        await store.confirm_stopped(ctx.run_id, ctx.attempt_id)
        return cohort_done()

    node_runner, _ = runner(store, calls, cohort_agent=superseded_meanwhile)
    session_id, [agent] = await graph(store, "cohort_agent")

    report = await run(node_runner, store, session_id, agent["node_id"])

    assert (report.status, report.code) == ("stale", "ATTEMPT_ENDED")
    assert (await current_run(store, session_id, agent["node_id"])).outputs is None


# ----------------------------------------------------------------------
# Suspensions
# ----------------------------------------------------------------------
def asks(question: str = "use wavelets?") -> AgentResult:
    return AgentResult(status=AgentStatus.SUSPENDED, suspension=Suspension(
        kind=SuspensionKind.ASK_USER, tool_use_id="toolu_1", payload={"question": question},
    ))


PAUSED = AgentResult(status=AgentStatus.SUSPENDED, suspension=Suspension(kind=SuspensionKind.PAUSE))


async def test_a_question_suspends_the_attempt_instead_of_failing_it(store, calls):
    node_runner, _ = runner(store, calls, cohort_agent=asks())
    session_id, [agent] = await graph(store, "cohort_agent")

    report = await run(node_runner, store, session_id, agent["node_id"])

    assert (report.status, report.code) == ("suspended", "ask_user")
    stored = await current_run(store, session_id, agent["node_id"])
    attempt = stored.attempts[0]
    assert stored.status == "running" and attempt.attempt_id == report.attempt_id
    assert attempt.state == "suspended" and attempt.error is None
    assert (attempt.suspension["tool_use_id"], attempt.suspension["payload"]) == (
        "toolu_1", {"question": "use wavelets?"})
    assert (await store.get_graph(session_id)).activity == "awaiting_user"


async def test_a_pause_suspends_with_no_tool_call(store, calls):
    node_runner, _ = runner(store, calls, cohort_agent=PAUSED)
    session_id, [agent] = await graph(store, "cohort_agent")

    report = await run(node_runner, store, session_id, agent["node_id"])

    assert (report.status, report.code) == ("suspended", "pause")
    suspension = (await current_run(store, session_id, agent["node_id"])).attempts[0].suspension
    assert suspension["kind"] == "pause" and "tool_use_id" not in suspension


async def test_a_cancel_requested_before_the_suspend_is_confirmed_as_a_stop(store, calls):
    async def cancelled_then_asks(spec, task, inputs, ctx, cancel) -> AgentResult:
        # The user pressed Stop, but the agent asked its question before the token fired.
        await store.request_cancel(ctx.run_id, ctx.attempt_id)
        return asks()

    node_runner, _ = runner(store, calls, cohort_agent=cancelled_then_asks)
    session_id, [agent] = await graph(store, "cohort_agent")

    report = await run(node_runner, store, session_id, agent["node_id"])

    assert report.status == "cancelled"
    assert (await current_run(store, session_id, agent["node_id"])).attempts[0].state == "cancelled"
    assert (await store.get_graph(session_id)).activity == "idle"  # nothing is left waiting


async def test_a_run_superseded_before_the_suspend_is_confirmed_as_a_stop(store, calls):
    session_id, [agent] = await graph(store, "cohort_agent")

    async def edited_then_asks(spec, task, inputs, ctx, cancel) -> AgentResult:
        # The user edited the node while it ran; the change restarts it.
        edited = {**agent, "params": {"task": "build it again"}}
        await submit(store, session_id, [edited], on_running="restart")
        return asks()

    node_runner, _ = runner(store, calls, cohort_agent=edited_then_asks)

    report = await run(node_runner, store, session_id, agent["node_id"])

    assert report.status == "cancelled"
    old_run = (await store.node_runs(agent["node_id"]))[0]
    assert old_run.superseded_at is not None and old_run.attempts[0].state == "cancelled"


# ----------------------------------------------------------------------
# Retries
# ----------------------------------------------------------------------
async def test_starting_a_failed_run_again_opens_a_new_attempt_of_the_same_run(store, calls):
    # The runner does not decide whether to retry; it only runs the attempt it is given.
    attempts = iter([AgentResult(status=AgentStatus.FAILED, error_code="error"), cohort_done()])

    async def flaky(*_: Any) -> AgentResult:
        return next(attempts)

    node_runner, _ = runner(store, calls, cohort_agent=flaky)
    session_id, [agent] = await graph(store, "cohort_agent")
    first = await run(node_runner, store, session_id, agent["node_id"])

    second = await run(node_runner, store, session_id, agent["node_id"])

    assert first.status == "failed" and second.status == "completed"
    assert second.run_id == first.run_id and second.attempt_id != first.attempt_id
    stored = await current_run(store, session_id, agent["node_id"])
    assert [a.state for a in stored.attempts] == ["failed", "succeeded"]


FAILED_ONCE = [AgentResult(status=AgentStatus.FAILED, error_code="error"), cohort_done()]


async def test_a_retry_of_a_resumable_run_continues_its_saved_session(store, calls):
    node_runner, engine = runner(store, calls, cohort_agent=FAILED_ONCE)
    session_id, [agent] = await graph(store, "cohort_agent")
    await run(node_runner, store, session_id, agent["node_id"])

    report = await run(node_runner, store, session_id, agent["node_id"])

    assert report.status == "completed"
    assert [call.resume for call in engine.calls] == [None, Resume.continue_()]


async def test_a_retry_of_a_run_that_keeps_no_session_starts_over(store, calls):
    node_runner, engine = runner(store, calls, stateless_agent=FAILED_ONCE)
    session_id, [agent] = await graph(store, "stateless_agent")
    await run(node_runner, store, session_id, agent["node_id"])

    report = await run(node_runner, store, session_id, agent["node_id"])

    assert report.status == "completed"
    assert [call.resume for call in engine.calls] == [None, None]


# ----------------------------------------------------------------------
# Resuming a suspended attempt
# ----------------------------------------------------------------------
async def suspended_node(store, calls, *results: AgentResult):
    """A one-node graph whose agent plays ``results`` in order; the first one suspends it."""
    node_runner, engine = runner(store, calls, cohort_agent=list(results))
    session_id, [agent] = await graph(store, "cohort_agent")
    first = await run(node_runner, store, session_id, agent["node_id"])
    assert first.status == "suspended"
    return node_runner, engine, session_id, agent["node_id"], first


async def resume(node_runner: NodeRunner, store, session_id: str, node_id: str, **options: Any):
    return await node_runner.resume(await store.get_graph(session_id), node_id, **options)


async def test_an_answered_question_resumes_the_same_attempt_with_the_answer(store, calls):
    node_runner, engine, session_id, node_id, first = await suspended_node(store, calls, asks(), cohort_done())
    await store.answer(first.run_id, first.attempt_id, "toolu_1", answer="yes, with wavelets")

    report = await resume(node_runner, store, session_id, node_id)

    assert (report.status, report.attempt_id) == ("completed", first.attempt_id)
    assert engine.calls[-1].resume == Resume("yes, with wavelets")
    assert engine.calls[-1].ctx.attempt_id == first.attempt_id
    stored = await current_run(store, session_id, node_id)
    assert [a.state for a in stored.attempts] == ["succeeded"]  # a suspension is not a new attempt
    assert stored.attempts[0].resumed_at is not None
    assert stored.outputs["ports"]["cohort"]["ref"]["ids"] == [COHORT_ID]


async def test_a_question_that_is_not_answered_yet_does_not_resume(store, calls):
    node_runner, engine, session_id, node_id, first = await suspended_node(store, calls, asks(), cohort_done())

    report = await resume(node_runner, store, session_id, node_id)

    assert (report.status, report.code) == ("not_started", "NOT_RESUMABLE")
    assert len(engine.calls) == 1
    assert (await current_run(store, session_id, node_id)).attempts[0].state == "suspended"


async def test_a_tool_approval_resumes_with_the_users_decision(store, calls):
    approval = AgentResult(status=AgentStatus.SUSPENDED, suspension=Suspension(
        kind=SuspensionKind.TOOL_APPROVAL, tool_use_id="toolu_7", payload={"tool": "save_cohort"},
    ))
    node_runner, engine, session_id, node_id, first = await suspended_node(
        store, calls, approval, cohort_done())
    await store.answer(first.run_id, first.attempt_id, "toolu_7", decision="reject", comment="not yet")

    report = await resume(node_runner, store, session_id, node_id)

    assert report.status == "completed"
    assert engine.calls[-1].resume == Resume("reject")


async def test_a_pause_resumes_with_a_plain_continue_once_the_session_is_active(store, calls):
    node_runner, engine, session_id, node_id, first = await suspended_node(store, calls, PAUSED, cohort_done())
    await store.set_control_state(session_id, "paused")
    still_paused = await resume(node_runner, store, session_id, node_id)
    await store.set_control_state(session_id, "active")

    report = await resume(node_runner, store, session_id, node_id)

    assert (still_paused.status, still_paused.code) == ("not_started", "NOT_RESUMABLE")
    assert (report.status, report.attempt_id) == ("completed", first.attempt_id)
    assert engine.calls[-1].resume == Resume.continue_()


async def test_a_resumed_agent_may_suspend_again_on_the_same_attempt(store, calls):
    second_question = AgentResult(status=AgentStatus.SUSPENDED, suspension=Suspension(
        kind=SuspensionKind.ASK_USER, tool_use_id="toolu_2", payload={"question": "and the bin width?"},
    ))
    node_runner, _, session_id, node_id, first = await suspended_node(
        store, calls, asks(), second_question, cohort_done())
    await store.answer(first.run_id, first.attempt_id, "toolu_1", answer="yes")

    report = await resume(node_runner, store, session_id, node_id)

    assert (report.status, report.attempt_id) == ("suspended", first.attempt_id)
    attempt = (await current_run(store, session_id, node_id)).attempts[0]
    assert attempt.suspension["tool_use_id"] == "toolu_2" and "resume" not in attempt.suspension


async def test_a_suspended_attempt_that_was_stopped_has_nothing_to_resume(store, calls):
    node_runner, engine, session_id, node_id, first = await suspended_node(store, calls, asks(), cohort_done())
    await store.request_cancel(first.run_id, first.attempt_id)  # confirmed at once: no work was running

    report = await resume(node_runner, store, session_id, node_id)

    assert (report.status, report.code) == ("not_started", "NOT_SUSPENDED")
    assert len(engine.calls) == 1


async def test_a_resumed_attempt_can_be_cancelled_like_any_running_one(store, calls):
    async def works_until_cancelled(spec, task, inputs, ctx, cancel, resume=None) -> AgentResult:
        if resume is None:
            return asks()
        calls.started.set()
        while not cancel.cancelled:  # like the engine, it stops once the token fires
            await asyncio.sleep(0.01)
        return AgentResult(status=AgentStatus.CANCELLED, error_code="cancelled")

    node_runner, _ = runner(store, calls, cohort_agent=works_until_cancelled)
    session_id, [agent] = await graph(store, "cohort_agent")
    first = await run(node_runner, store, session_id, agent["node_id"])
    await store.answer(first.run_id, first.attempt_id, "toolu_1", answer="yes")
    cancel = CancelToken()

    task = asyncio.create_task(resume(node_runner, store, session_id, agent["node_id"], cancel=cancel))
    await asyncio.wait_for(calls.started.wait(), 1)
    await store.request_cancel(first.run_id, first.attempt_id)
    cancel.cancel()
    report = await asyncio.wait_for(task, 1)

    assert (report.status, report.attempt_id) == ("cancelled", first.attempt_id)
    assert (await current_run(store, session_id, agent["node_id"])).attempts[0].state == "cancelled"


# ----------------------------------------------------------------------
# Usage
# ----------------------------------------------------------------------
async def test_the_attempt_records_the_tokens_its_agent_spent(store, calls):
    spent = AgentResult(status=AgentStatus.COMPLETED, output={"cohort_id": COHORT_ID, "size": 12},
                        usage=Usage(prompt_tokens=1200, completion_tokens=300))
    node_runner, _ = runner(store, calls, cohort_agent=spent)
    session_id, [agent] = await graph(store, "cohort_agent")

    report = await run(node_runner, store, session_id, agent["node_id"])

    assert report.usage.total_tokens == 1500
    usage = (await current_run(store, session_id, agent["node_id"])).attempts[0].usage
    assert (usage["input_tokens"], usage["output_tokens"]) == (1200, 300)


async def test_a_failed_or_stopped_attempt_records_its_tokens_too(store, calls):
    failed = AgentResult(status=AgentStatus.FAILED, error_code="max_iterations", usage=Usage(500, 50))

    async def stops(spec, task, inputs, ctx, cancel) -> AgentResult:
        cancel.cancel()
        return AgentResult(status=AgentStatus.CANCELLED, error_code="cancelled", usage=Usage(40, 2))

    node_runner, _ = runner(store, calls, cohort_agent=failed, stateless_agent=stops)
    session_id, [agent] = await graph(store, "cohort_agent")
    other_session, [other] = await graph(store, "stateless_agent")

    await run(node_runner, store, session_id, agent["node_id"])
    await run(node_runner, store, other_session, other["node_id"])

    failed_usage = (await current_run(store, session_id, agent["node_id"])).attempts[0].usage
    stopped_usage = (await current_run(store, other_session, other["node_id"])).attempts[0].usage
    assert (failed_usage["input_tokens"], stopped_usage["input_tokens"]) == (500, 40)


async def test_a_tool_attempt_spends_no_tokens_and_records_none(store, calls):
    node_runner, _ = runner(store, calls)
    session_id, [agent, tool] = await graph(store, "cohort_agent", "extract_features")
    await run(node_runner, store, session_id, agent["node_id"])

    await run(node_runner, store, session_id, tool["node_id"])

    assert (await current_run(store, session_id, tool["node_id"])).attempts[0].usage is None


# ----------------------------------------------------------------------
# The agent's view
# ----------------------------------------------------------------------
class Sink:
    """Records the view events sent to one session."""

    def __init__(self, fail: bool = False) -> None:
        self.views: list[Any] = []
        self.fail = fail

    async def emit(self, event: Any) -> None:
        pass

    async def emit_view(self, event: Any) -> None:
        if self.fail:
            raise RuntimeError("socket gone")
        self.views.append(event)


def viewing_runner(store, calls, sink: Sink, **scripts: Any) -> NodeRunner:
    engine = FakeAgentEngine({"cohort_agent": cohort_done(), **scripts})
    return NodeRunner(store, catalog(calls), engine=engine, sink_for=lambda _session_id: sink)


COHORT_OUTPUT = {"cohort_id": COHORT_ID, "size": 12, "warnings": ["3 volumes have no label"]}


async def test_a_completed_agent_shows_its_structured_output_in_its_view(store, calls):
    sink = Sink()
    done = AgentResult(status=AgentStatus.COMPLETED, output=COHORT_OUTPUT)
    node_runner = viewing_runner(store, calls, sink, study_population_planner_agent=done)
    session_id, [agent] = await graph(store, "study_population_planner_agent")

    report = await run(node_runner, store, session_id, agent["node_id"])

    [view] = sink.views
    assert view.type == "population_output"
    assert {key: view.payload[key] for key in ("agent_id", "run_id", "node_id", "status")} == {
        "agent_id": "study_population_planner_agent", "run_id": report.run_id, "node_id": agent["node_id"],
        "status": "completed"}
    assert view.payload["outputs"] == COHORT_OUTPUT
    assert view.payload["warnings"] == ["3 volumes have no label"] and view.payload["errors"] == []


async def test_an_agent_with_no_view_and_a_tool_node_send_none_from_the_runner(store, calls):
    sink = Sink()
    node_runner = viewing_runner(store, calls, sink)
    session_id, [agent, tool] = await graph(store, "cohort_agent", "extract_features")

    await run(node_runner, store, session_id, agent["node_id"])
    await run(node_runner, store, session_id, tool["node_id"])

    assert sink.views == []


async def test_an_agent_that_did_not_complete_shows_no_view(store, calls):
    sink = Sink()
    node_runner = viewing_runner(store, calls, sink, study_population_planner_agent=asks())
    session_id, [agent] = await graph(store, "study_population_planner_agent")

    report = await run(node_runner, store, session_id, agent["node_id"])

    assert report.status == "suspended" and sink.views == []


async def test_a_view_that_cannot_be_sent_does_not_undo_the_result(store, calls):
    done = AgentResult(status=AgentStatus.COMPLETED, output=COHORT_OUTPUT)
    node_runner = viewing_runner(store, calls, Sink(fail=True), study_population_planner_agent=done)
    session_id, [agent] = await graph(store, "study_population_planner_agent")

    report = await run(node_runner, store, session_id, agent["node_id"])

    assert report.status == "completed"
    assert (await current_run(store, session_id, agent["node_id"])).status == "completed"
