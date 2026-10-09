"""The Diff Engine (merged plan, Phase 8.4): the §27 matrix on pure states, and
apply / decide through InMemoryGraphStore."""

from __future__ import annotations

import uuid

import pytest

from agent_system.execution.diff_engine import DiffEngine, main_idempotency_key
from agent_system.execution.in_memory_graph_store import InMemoryGraphStore
from agent_system.planning.graph_model import (
    AddNode,
    GraphDiff,
    RemoveNode,
    RerunNode,
    SeedFrom,
    UpdateParams,
)
from tests.support.graph_fixtures import POOLS, live, node, research_catalog, state
from tests.support.graph_store_scenarios import run_ok, start

pytestmark = pytest.mark.unit

CATALOG = research_catalog()


def engine(store=None, woken=None) -> DiffEngine:
    return DiffEngine(store or InMemoryGraphStore(), CATALOG, resource_pools=POOLS,
                      on_applied=(woken.append if woken is not None else None))


# ----------------------------------------------------------------------
# The §27 matrix: 4 states x affected/unaffected x on_running
# ----------------------------------------------------------------------
@pytest.mark.parametrize("on_running", ["reject", "restart"])
@pytest.mark.parametrize("touch", ["affected", "unaffected"])
@pytest.mark.parametrize("status", [None, "running", "completed", "failed"])
def test_the_section_27_matrix(status, touch, on_running):
    target = node("build_cohort")
    graph = state(live(target, status))
    if touch == "affected":
        op = UpdateParams(node_id=target.node_id, params={"criteria": "MR"})
    else:
        op = AddNode(kind="tool", ref="build_cohort", params={"criteria": "MR"})
    outcome = engine().plan_change(graph, GraphDiff(base_version=3, ops=[op], on_running=on_running))

    if touch == "unaffected" or status is None:
        # It stays (or its definition is updated): nothing to supersede or cancel.
        assert outcome.ok and outcome.runs_to_supersede == []
        assert (target.node_id in outcome.affected) == (touch == "affected")
        return
    if status == "running" and on_running == "reject":
        assert [e.code for e in outcome.errors] == ["RUNNING_NODE_AFFECTED"]
        return
    assert outcome.ok
    [superseded] = outcome.runs_to_supersede
    assert superseded.run_id == graph.nodes[0].run.run_id
    assert superseded.cancel is (status == "running")
    assert superseded.supersedes_completed is (status == "completed")
    assert outcome.approval_reasons == (("supersedes_completed",) if status == "completed" else ())


def test_a_to_new_to_b_affects_b_only():
    a, b = node("build_cohort"), node("segmentation_agent")
    graph = state(live(a, "completed"), live(b, "completed"))
    new_id = uuid.uuid4()
    outcome = engine().plan_change(graph, GraphDiff(base_version=3, ops=[
        AddNode(node_id=new_id, kind="agent", ref="literature_agent", params={"task": "search"}, at=1),
    ]))
    assert outcome.ok and outcome.affected == [new_id, b.node_id]
    assert [s.node_id for s in outcome.runs_to_supersede] == [b.node_id]
    assert outcome.approval_reasons == ("supersedes_completed",)


def test_a_stale_base_is_refused_before_anything_else():
    outcome = engine().plan_change(state(version=5), GraphDiff(base_version=4, ops=[
        AddNode(kind="tool", ref="build_cohort", params={"criteria": "x"})]))
    assert [e.code for e in outcome.errors] == ["STALE_BASE"] and outcome.content is None


def test_validator_errors_come_back_with_the_content():
    outcome = engine().plan_change(state(), GraphDiff(base_version=3, ops=[
        AddNode(kind="agent", ref="segmentation_agent", params={"task": "x"})]))
    assert [e.code for e in outcome.errors] == ["MISSING_INPUT"] and outcome.content is not None


def test_add_node_takes_the_catalog_version():
    outcome = engine().plan_change(state(), GraphDiff(base_version=3, ops=[
        AddNode(kind="tool", ref="build_cohort", params={"criteria": "x"})]))
    assert outcome.content.nodes[0].ref_version == "1"


# ----------------------------------------------------------------------
# Through the store
# ----------------------------------------------------------------------
async def _graph(store, session_id):
    return await store.get_graph(session_id)


def _add(ref: str, **fields):
    params = {"task": "go"} if ref.endswith("_agent") else {"criteria": "chest CT"}
    return AddNode(kind="agent" if ref.endswith("_agent") else "tool", ref=ref, params=params, **fields)


@pytest.mark.asyncio
async def test_an_auto_change_applies_and_wakes_the_executor():
    store, woken = InMemoryGraphStore(), []
    session_id = await store.create_session("q")
    first, second = _add("build_cohort"), _add("segmentation_agent")
    result = await engine(store, woken).apply(session_id, GraphDiff(base_version=0, ops=[first, second]),
                                              author="user", idempotency_key="k1")
    assert result.ok and result.status == "applied" and result.change.version == 1
    assert set(result.change.node_ids) == {str(first.node_id), str(second.node_id)}
    assert woken == [session_id]
    graph = await _graph(store, session_id)
    assert [item.node.node_id for item in graph.nodes] == [str(first.node_id), str(second.node_id)]


@pytest.mark.asyncio
async def test_invalid_diffs_never_reach_the_store():
    store, woken = InMemoryGraphStore(), []
    session_id = await store.create_session("q")
    result = await engine(store, woken).apply(session_id, GraphDiff(base_version=0, ops=[
        _add("segmentation_agent")]), author="user", idempotency_key="k1")
    assert not result.ok and [e.code for e in result.errors] == ["MISSING_INPUT"]
    assert (await _graph(store, session_id)).version == 0 and woken == []


@pytest.mark.asyncio
async def test_superseding_a_completed_run_waits_for_a_decision_and_keeps_the_old_run():
    store, woken = InMemoryGraphStore(), []
    diff_engine = engine(store, woken)
    session_id = await store.create_session("q")
    cohort = _add("build_cohort")
    await diff_engine.apply(session_id, GraphDiff(base_version=0, ops=[cohort]), author="user",
                            idempotency_key="k1")
    started = await start(store, session_id, str(cohort.node_id))
    await run_ok(store, started)

    edit = GraphDiff(base_version=1, ops=[UpdateParams(node_id=cohort.node_id, params={"criteria": "MR"})])
    proposed = await diff_engine.apply(session_id, edit, author="user", idempotency_key="k2")
    assert proposed.status == "pending" and "supersedes_completed" in proposed.change.change.approval_reasons
    assert woken == [session_id]  # only the first, applied change woke it

    change = proposed.change.change
    decided = await diff_engine.decide(change.change_id, "approve", change.content_digest, session_id=session_id)
    assert decided.change.status == "applied" and decided.superseded_run_ids == [started.run.run_id]
    assert woken == [session_id, session_id]
    history = await store.node_runs(str(cohort.node_id))
    assert [run.run_id for run in history] == [started.run.run_id]  # superseded, never deleted
    assert history[0].superseded_at is not None and history[0].status == "completed"


@pytest.mark.asyncio
async def test_a_restart_supersedes_a_running_node_and_requests_its_cancel():
    store = InMemoryGraphStore()
    diff_engine = engine(store)
    session_id = await store.create_session("q")
    cohort = _add("build_cohort")
    await diff_engine.apply(session_id, GraphDiff(base_version=0, ops=[cohort]), author="user",
                            idempotency_key="k1")
    started = await start(store, session_id, str(cohort.node_id))
    await store.mark_dispatched(started.run.run_id, started.attempt.attempt_id, "running")
    edit = [UpdateParams(node_id=cohort.node_id, params={"criteria": "MR"})]

    rejected = await diff_engine.apply(session_id, GraphDiff(base_version=1, ops=edit), author="user",
                                       idempotency_key="k2")
    assert [e.code for e in rejected.errors] == ["RUNNING_NODE_AFFECTED"]

    restarted = await diff_engine.apply(session_id, GraphDiff(base_version=1, ops=edit, on_running="restart"),
                                        author="user", idempotency_key="k3")
    assert restarted.status == "applied" and restarted.change.superseded_run_ids == [started.run.run_id]
    [run] = await store.node_runs(str(cohort.node_id))
    assert run.attempts[-1].cancel_requested_at is not None


@pytest.mark.asyncio
async def test_the_main_submits_once_per_tool_call():
    store, woken = InMemoryGraphStore(), []
    diff_engine = engine(store, woken)
    session_id = await store.create_session("q")
    diff = GraphDiff(base_version=0, ops=[_add("build_cohort")])
    first = await diff_engine.apply(session_id, diff, author="main", turn_id=7, call_id="call_0", tool="execute")
    assert first.status == "applied" and woken == [session_id]
    [stored] = store.changes.values()
    assert stored["idempotency_key"] == main_idempotency_key(7, "call_0")
    assert main_idempotency_key(7, "call_0") != main_idempotency_key(8, "call_0")

    # The key replays the same submission: the store returns the change it already has.
    replay = await store.submit_change(
        session_id, author="main", base_version=0, content=first.outcome.content.document(),
        tool_call={"turn_id": 7, "call_id": "call_0", "tool": "execute"},
    )
    assert replay.replayed and replay.change.change_id == first.change.change.change_id
    # Re-handling the turn against the moved graph submits nothing new.
    again = await diff_engine.apply(session_id, diff, author="main", turn_id=7, call_id="call_0", tool="execute")
    assert [e.code for e in again.errors] == ["STALE_BASE"] and len(store.changes) == 1
    with pytest.raises(ValueError):
        await diff_engine.apply(session_id, diff, author="main")


@pytest.mark.asyncio
async def test_a_stale_base_from_the_store_and_new_ids_stay_unused():
    store = InMemoryGraphStore()
    diff_engine = engine(store)
    session_id = await store.create_session("q")
    cohort = _add("build_cohort")
    await diff_engine.apply(session_id, GraphDiff(base_version=0, ops=[cohort]), author="user",
                            idempotency_key="k1")
    stale = await diff_engine.apply(session_id, GraphDiff(base_version=0, ops=[_add("build_cohort")]),
                                    author="user", idempotency_key="k2")
    assert [e.code for e in stale.errors] == ["STALE_BASE"]

    await diff_engine.apply(session_id, GraphDiff(base_version=1, ops=[RemoveNode(node_id=cohort.node_id)]),
                            author="user", idempotency_key="k3")
    reused = _add("build_cohort", node_id=cohort.node_id)
    refused = await diff_engine.apply(session_id, GraphDiff(base_version=2, ops=[reused]), author="user",
                                      idempotency_key="k4")
    assert [e.code for e in refused.errors] == ["INVALID_CHANGE"] and "DUPLICATE_NODE" in refused.errors[0].detail


@pytest.mark.asyncio
async def test_a_seeded_rerun_names_an_earlier_run_of_the_same_resumable_node():
    store = InMemoryGraphStore()
    diff_engine = engine(store)
    session_id = await store.create_session("q")
    pop = _add("population_agent")
    await diff_engine.apply(session_id, GraphDiff(base_version=0, ops=[pop]), author="user",
                            idempotency_key="k1")
    started = await start(store, session_id, str(pop.node_id), prompt="find patients")
    await run_ok(store, started)

    good = SeedFrom(run_id=uuid.UUID(started.run.run_id), checkpoint_id="cp-3")
    seeded = await diff_engine.apply(session_id, GraphDiff(base_version=1, ops=[
        RerunNode(node_id=pop.node_id, seed_from=good)]), author="user", idempotency_key="k2")
    assert seeded.status == "pending"  # it supersedes a completed run, like any rerun
    assert seeded.outcome.runs_to_supersede[0].supersedes_completed
    content = seeded.change.change.content
    assert content["rerun_node_ids"] == [str(pop.node_id)]
    assert content["nodes"][0]["seed_from"]["run_id"] == started.run.run_id

    stranger = SeedFrom(run_id=uuid.uuid4(), checkpoint_id="cp-3")
    refused = await diff_engine.apply(session_id, GraphDiff(base_version=1, ops=[
        RerunNode(node_id=pop.node_id, seed_from=stranger)]), author="user", idempotency_key="k3")
    assert [e.code for e in refused.errors] == ["BAD_SEED"]
