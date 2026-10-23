"""NodeRunner tests, with InMemoryGraphStore, FakeAgentEngine and inline tools."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from pydantic import BaseModel

from agent_system.execution.in_memory_graph_store import InMemoryGraphStore
from agent_system.execution.node_runner import NodeRunner
from agent_system.toolkit.agents.engine import AgentResult, AgentStatus, FakeAgentEngine
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
