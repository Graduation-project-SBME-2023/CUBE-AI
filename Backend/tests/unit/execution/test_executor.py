"""SessionExecutor tests, with InMemoryGraphStore, FakeAgentEngine and real pools."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

import pytest

from agent_system.execution.executor import FakeExecutor, SessionExecutor
from agent_system.execution.graph_store import GraphStoreError
from agent_system.execution.in_memory_graph_store import InMemoryGraphStore
from agent_system.execution.node_runner import NodeRunner
from agent_system.execution.resources import Pools
from agent_system.toolkit.agents.engine import (
    AgentResult,
    AgentStatus,
    FakeAgentEngine,
    Resume,
    Suspension,
    SuspensionKind,
)
from agent_system.toolkit.agents.spec import AgentSpec
from agent_system.toolkit.ports import ExtractedArtifact, PortSpec
from tests.support.graph_store_scenarios import node, submit
from tests.support.toolkit_fixtures import make_catalog

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

COHORT_ID = str(uuid.uuid4())


def _cohort(result: Any) -> ExtractedArtifact:
    return ExtractedArtifact(ref={"cohort_id": COHORT_ID}, summary={"size": 12, "frozen": True})


def catalog():
    cohort = {"cohort": PortSpec("Cohort", extract=_cohort)}
    return make_catalog(
        AgentSpec(agent_type="cohort_agent", description="Builds a cohort.", kind="execution",
                  system_prompt="Work.", outputs=cohort),
        AgentSpec(agent_type="segment_agent", description="Segments the cohort.", kind="execution",
                  system_prompt="Work.", inputs={"cohort": PortSpec("Cohort")}),
        AgentSpec(agent_type="model_agent", description="Models the cohort.", kind="execution",
                  system_prompt="Work.", inputs={"cohort": PortSpec("Cohort")}),
    )


DONE = AgentResult(status=AgentStatus.COMPLETED, output={})
ASKS = AgentResult(status=AgentStatus.SUSPENDED, suspension=Suspension(
    kind=SuspensionKind.ASK_USER, tool_use_id="toolu_1", payload={"question": "use wavelets?"}))


class Harness:
    """One session, its store, its engine and its executor."""

    def __init__(self, *, pools: Optional[Pools] = None, store: Optional[InMemoryGraphStore] = None,
                 **scripts: Any) -> None:
        self.store = store or InMemoryGraphStore()
        self.engine = FakeAgentEngine({"cohort_agent": DONE, "segment_agent": DONE, "model_agent": DONE,
                                       **scripts})
        self.pools = pools or Pools({"llm": 4})
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.runner = NodeRunner(self.store, catalog(), engine=self.engine)
        self.session_id = ""
        self.executor: Optional[SessionExecutor] = None

    async def publish(self, event_type: str, payload: Any, node_id: Optional[str]) -> None:
        self.events.append((event_type, dict(payload)))

    async def graph(self, *refs: str, **fields: Any) -> list[dict[str, Any]]:
        self.session_id = await self.store.create_session("q")
        nodes = [node(ref, "agent", params={"task": "do it"}, **fields.get(ref, {})) for ref in refs]
        await submit(self.store, self.session_id, nodes)
        self.executor = SessionExecutor(self.session_id, self.store, self.runner, self.pools,
                                        publish=self.publish)
        return nodes

    async def run(self) -> None:
        self.executor.start()
        await asyncio.wait_for(self.executor.join(), 2)

    async def runs(self, node_id: str):
        return await self.store.node_runs(node_id)

    def ran(self) -> list[str]:
        return [call.spec.name for call in self.engine.calls]


async def settle() -> None:
    """Let the executor's tasks run until they wait."""
    for _ in range(20):
        await asyncio.sleep(0)


# ----------------------------------------------------------------------
# Order and pins
# ----------------------------------------------------------------------
async def test_a_three_node_graph_runs_in_order_and_the_loop_exits_when_idle():
    h = Harness()
    a, b, c = await h.graph("cohort_agent", "segment_agent", "model_agent")

    await h.run()

    assert h.ran() == ["cohort_agent", "segment_agent", "model_agent"]
    assert [r.status for r in h.executor.reports] == ["completed"] * 3
    assert not h.executor.running
    assert (await h.store.get_graph(h.session_id)).next_step is None


async def test_each_node_consumes_the_run_that_completed_before_it():
    h = Harness()
    a, b, _ = await h.graph("cohort_agent", "segment_agent", "model_agent")

    await h.run()

    [first] = await h.runs(a["node_id"])
    [second] = await h.runs(b["node_id"])
    assert second.resolved_inputs["ports"]["cohort"]["run_id"] == first.run_id


# ----------------------------------------------------------------------
# Wake-ups
# ----------------------------------------------------------------------
async def test_an_idle_executor_starts_again_when_the_graph_changes():
    h = Harness()
    a, = await h.graph("cohort_agent")
    await h.run()
    b = node("segment_agent", "agent", params={"task": "do it"})

    await submit(h.store, h.session_id, [a, b])
    h.executor.wake("graph_changed")
    await asyncio.wait_for(h.executor.join(), 2)

    assert h.ran() == ["cohort_agent", "segment_agent"]


async def test_a_change_made_while_the_graph_is_read_is_not_lost():
    store = InMemoryGraphStore()
    h = Harness(store=store)
    await h.graph()  # an empty graph
    original = store.get_graph
    added = node("cohort_agent", "agent", params={"task": "do it"})
    pending = [added]

    async def get_graph_while_the_user_adds_a_node(session_id: str):
        view = await original(session_id)  # read before the change
        if pending:
            await submit(store, session_id, [pending.pop()])
            h.executor.wake("graph_changed")
        return view

    store.get_graph = get_graph_while_the_user_adds_a_node
    await h.run()

    assert h.ran() == ["cohort_agent"]


async def test_a_start_the_store_refuses_ends_quietly_and_the_next_wake_reads_the_graph_again():
    store = InMemoryGraphStore()
    h = Harness(store=store)
    a, = await h.graph("cohort_agent")
    original = store.start_node
    refusals = iter([GraphStoreError("STALE_DEFINITION", "the node changed")])

    async def refuse_once(request):
        for refusal in refusals:
            raise refusal
        return await original(request)

    store.start_node = refuse_once
    await h.run()
    assert ([r.status for r in h.executor.reports], h.ran()) == (["not_started"], [])  # no busy retry

    h.executor.wake("graph_changed")
    await asyncio.wait_for(h.executor.join(), 2)

    assert [r.status for r in h.executor.reports] == ["not_started", "completed"]


# ----------------------------------------------------------------------
# Pools
# ----------------------------------------------------------------------
async def test_a_full_pool_makes_the_node_wait_and_a_release_starts_it():
    pools = Pools({"llm": 1})
    h = Harness(pools=pools)
    await h.graph("cohort_agent")
    held = await pools.try_acquire({"llm"})  # another session's agent holds the only slot

    h.executor.start()
    await settle()
    assert h.ran() == [] and h.executor.running
    h.executor.wake("graph_changed")  # an unrelated wake does not repeat the refusal event
    await settle()
    assert [event for event, _ in h.events] == ["node_start_refused"]
    assert h.events[0][1]["pools"] == ["llm"]

    held.release()
    await asyncio.wait_for(h.executor.join(), 2)

    assert h.ran() == ["cohort_agent"] and pools.available("llm")


async def test_two_sessions_share_a_pool_one_node_at_a_time():
    pools = Pools({"llm": 1})
    gate = asyncio.Event()
    active: list[int] = []
    most = 0

    async def slow(*_: Any) -> AgentResult:
        nonlocal most
        active.append(1)
        most = max(most, len(active))
        await gate.wait()
        active.pop()
        return DONE

    first, second = Harness(pools=pools, cohort_agent=slow), Harness(pools=pools, cohort_agent=slow)
    await first.graph("cohort_agent")
    await second.graph("cohort_agent")
    first.executor.start()
    second.executor.start()
    await settle()
    gate.set()
    await asyncio.wait_for(asyncio.gather(first.executor.join(), second.executor.join()), 2)

    assert most == 1 and first.ran() == second.ran() == ["cohort_agent"]


# ----------------------------------------------------------------------
# Pause, resume, stop
# ----------------------------------------------------------------------
async def test_a_paused_session_starts_nothing_until_it_is_resumed():
    h = Harness()
    await h.graph("cohort_agent", "segment_agent")
    await h.executor.pause()
    await asyncio.wait_for(h.executor.join(), 2)
    assert h.ran() == []

    await h.executor.resume()
    await asyncio.wait_for(h.executor.join(), 2)

    assert h.ran() == ["cohort_agent", "segment_agent"]


async def test_a_pause_lets_the_running_node_finish_and_starts_no_other():
    gate = asyncio.Event()

    async def slow(*_: Any) -> AgentResult:
        await gate.wait()
        return DONE

    h = Harness(cohort_agent=slow)
    await h.graph("cohort_agent", "segment_agent")
    h.executor.start()
    await settle()
    await h.executor.pause()
    gate.set()
    await asyncio.wait_for(h.executor.join(), 2)

    assert h.ran() == ["cohort_agent"] and h.executor.reports[0].status == "completed"


async def test_stop_cancels_the_running_node_and_nothing_starts_after_it():
    async def until_cancelled(spec, task, inputs, ctx, cancel) -> AgentResult:
        while not cancel.cancelled:
            await asyncio.sleep(0.01)
        return AgentResult(status=AgentStatus.CANCELLED, error_code="cancelled")

    h = Harness(cohort_agent=until_cancelled)
    a, _ = await h.graph("cohort_agent", "segment_agent")
    h.executor.start()
    await settle()

    await h.executor.stop()
    await asyncio.wait_for(h.executor.join(), 2)

    assert [r.status for r in h.executor.reports] == ["cancelled"]
    assert h.ran() == ["cohort_agent"]
    assert (await h.runs(a["node_id"]))[0].status == "cancelled"


# ----------------------------------------------------------------------
# Suspensions and releases
# ----------------------------------------------------------------------
async def test_a_question_frees_the_pool_and_the_answer_resumes_the_same_attempt():
    pools = Pools({"llm": 1})
    h = Harness(pools=pools, cohort_agent=[ASKS, DONE])
    a, _ = await h.graph("cohort_agent", "segment_agent")
    await h.run()
    assert [r.status for r in h.executor.reports] == ["suspended"]
    assert pools.available("llm") and not h.executor.running  # nothing waits in memory

    first = h.executor.reports[0]
    await h.store.answer(first.run_id, first.attempt_id, "toolu_1", answer="yes")
    h.executor.wake("gate_resolved")
    await asyncio.wait_for(h.executor.join(), 2)

    assert [r.status for r in h.executor.reports] == ["suspended", "completed", "completed"]
    assert h.executor.reports[1].attempt_id == first.attempt_id
    assert h.engine.calls[1].resume == Resume("yes")
    assert h.ran() == ["cohort_agent", "cohort_agent", "segment_agent"]


async def test_a_node_the_user_must_release_is_announced_once_and_runs_after_the_release():
    h = Harness()
    a, = await h.graph("cohort_agent", cohort_agent={"approval": "before"})
    await h.run()
    h.executor.wake("graph_changed")  # nothing changed for the node: no second announcement
    await asyncio.wait_for(h.executor.join(), 2)
    assert h.ran() == []
    assert [event for event, _ in h.events] == ["approval_requested"]

    await submit(h.store, h.session_id, [a], release_hold_node_ids=[a["node_id"]])
    h.executor.wake("graph_changed")
    await asyncio.wait_for(h.executor.join(), 2)

    assert h.ran() == ["cohort_agent"]


# ----------------------------------------------------------------------
# The fake for callers
# ----------------------------------------------------------------------
async def test_the_fake_executor_records_the_control_calls_in_order():
    fake = FakeExecutor("s1")
    fake.start()
    fake.wake("graph_changed")
    await fake.pause()
    await fake.resume()
    await fake.stop()

    assert fake.calls == [("start", None), ("wake", "graph_changed"), ("pause", None), ("resume", None),
                          ("stop", None)]
