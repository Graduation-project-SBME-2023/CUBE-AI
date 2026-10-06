"""One scenario table for every GraphStore.

Each scenario drives a store through the public protocol and asserts the
outcome. ``tests/unit/execution/test_graph_store_scenarios.py`` runs them on
``InMemoryGraphStore``; ``tests/integration/test_v1_p_graph_store_live.py`` runs
the same ones on ``HttpGraphStore`` against PostgreSQL. A rule the fake did not
enforce, or enforced differently, fails one of the two.
"""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable, Optional

import pytest

from agent_system.execution.graph_store import GraphStore, GraphStoreError, StartRequest


def node(ref: str = "run_modeling", kind: str = "tool", **fields: Any) -> dict[str, Any]:
    return {"node_id": str(uuid.uuid4()), "kind": kind, "ref": ref, "ref_version": "1", "title": ref,
            "params": {}, **fields}


async def submit(store: GraphStore, session_id: str, nodes: list[dict], *, reasons=(), **content: Any):
    version = (await store.get_graph(session_id)).version
    return await store.submit_change(
        session_id, author="user", base_version=version, content={"v": 1, "nodes": nodes, **content},
        approval_reasons=tuple(reasons), idempotency_key=str(uuid.uuid4()),
    )


async def approve(store: GraphStore, result) -> Any:
    return await store.decide_change(result.change.change_id, "approve", result.change.content_digest)


async def start(store: GraphStore, session_id: str, node_id: str, *, pins: Optional[dict] = None,
                prompt: Optional[str] = None, explicit: bool = False, spec: Optional[str] = None, **limits: Any):
    graph = await store.get_graph(session_id)
    live = next(item.node for item in graph.nodes if item.node.node_id == node_id)
    return await store.start_node(StartRequest(
        node_id=node_id, accepted_by_change_id=live.accepted_by_change_id, limits=limits,
        resolved_inputs={"v": 1, "ports": pins or {}}, compiled_prompt=prompt, explicit_retry=explicit,
        spec_digest=spec,
    ))


async def run_ok(store: GraphStore, started, summary: Optional[dict] = None) -> None:
    run_id, attempt_id = started.run.run_id, started.attempt.attempt_id
    await store.mark_dispatched(run_id, attempt_id, "running")
    await store.complete_attempt(run_id, attempt_id, {"v": 1, "ports": {"summary": {
        "type": "ResultSummary", "ref": {"table": "node_runs", "ids": [run_id]}, "summary": summary or {},
    }}})


async def refused(code: str, awaitable: Awaitable) -> None:
    with pytest.raises(GraphStoreError) as caught:
        await awaitable
    assert caught.value.code == code, f"expected {code}, got {caught.value.code}: {caught.value.message}"


def checkpoint(store_id: str, spec: str = "s1") -> dict[str, Any]:
    return {"checkpoint_id": store_id, "checkpoint": b"state", "type": "msgpack",
            "metadata": {"v": 1, "spec_digest": spec, "engine_version": "1", "pending_tools": []}}


# ----------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------
async def auto_change_applies_and_cas_refuses_a_stale_base(store: GraphStore) -> None:
    session_id = await store.create_session("q")
    result = await submit(store, session_id, [node(), node()])
    assert (result.change.status, result.change.decided_by, result.version) == ("applied", "auto", 1)
    await refused("STALE_BASE", store.submit_change(
        session_id, author="user", base_version=0, content={"v": 1, "nodes": []}, idempotency_key="k"))
    graph = await store.get_graph(session_id)
    assert [item.node.node_seq for item in graph.nodes] == [1, 2] and graph.activity == "idle"


async def plan_mode_waits_for_a_decision_by_digest(store: GraphStore) -> None:
    session_id = await store.create_session("q", execution_mode="plan")
    first = await submit(store, session_id, [node()])
    second = await submit(store, session_id, [node()])
    assert first.change.status == second.change.status == "pending"
    assert "PLAN_MODE" in second.change.approval_reasons
    graph = await store.get_graph(session_id)
    assert graph.pending_change.change_id == second.change.change_id and graph.activity == "awaiting_approval"
    await refused("DIGEST_MISMATCH", store.decide_change(second.change.change_id, "approve", "0" * 64))
    applied = await approve(store, second)
    assert (applied.change.status, applied.version) == ("applied", 1)


async def one_attempt_at_a_time_and_readiness(store: GraphStore) -> None:
    session_id = await store.create_session("q")
    a, b = node(), node()
    await submit(store, session_id, [a, b])
    await refused("NOT_READY", start(store, session_id, b["node_id"]))
    started = await start(store, session_id, a["node_id"])
    assert started.first_start and started.attempt.state == "preparing"
    await refused("ATTEMPT_ACTIVE", start(store, session_id, a["node_id"]))
    await run_ok(store, started)
    pin = {"in": {"node_id": a["node_id"], "run_id": str(uuid.uuid4()), "port": "summary"}}
    await refused("BAD_PIN", start(store, session_id, b["node_id"], pins=pin))
    pin["in"]["run_id"] = started.run.run_id
    downstream = await start(store, session_id, b["node_id"], pins=pin)
    assert downstream.run.resolved_inputs["ports"]["in"]["type"] == "ResultSummary"


async def a_rerun_supersedes_and_keeps_old_pins(store: GraphStore) -> None:
    session_id = await store.create_session("q")
    a, b = node(), node()
    await submit(store, session_id, [a, b])
    first = await start(store, session_id, a["node_id"])
    await run_ok(store, first)
    pin = {"in": {"node_id": a["node_id"], "run_id": first.run.run_id, "port": "summary"}}
    second = await start(store, session_id, b["node_id"], pins=pin)
    await run_ok(store, second)
    rerun = await submit(store, session_id, [a, b], rerun_node_ids=[a["node_id"]])
    assert rerun.change.status == "pending" and "supersedes_completed" in rerun.change.approval_reasons
    applied = await approve(store, rerun)
    assert set(applied.superseded_run_ids) == {first.run.run_id, second.run.run_id}
    fresh = await start(store, session_id, a["node_id"])
    assert fresh.first_start and fresh.run.execution_policy["fresh"] is True


async def a_running_node_is_affected_only_on_restart(store: GraphStore) -> None:
    session_id = await store.create_session("q")
    a = node()
    await submit(store, session_id, [a])
    started = await start(store, session_id, a["node_id"])
    await store.mark_dispatched(started.run.run_id, started.attempt.attempt_id, "running")
    edited = {**a, "params": {"x": 1}}
    await refused("INVALID_CHANGE", submit(store, session_id, [edited]))
    applied = await submit(store, session_id, [edited], on_running="restart")
    assert applied.superseded_run_ids == [started.run.run_id]
    graph = await store.get_graph(session_id)
    assert graph.nodes[0].run is None  # superseded: no current run until the next start
    await refused("ATTEMPT_ACTIVE", start(store, session_id, a["node_id"]))  # still stopping
    stopped = await store.confirm_stopped(started.run.run_id, started.attempt.attempt_id)
    assert stopped.state == "cancelled"
    await start(store, session_id, a["node_id"])


async def retries_stop_at_the_attempt_limit(store: GraphStore) -> None:
    session_id = await store.create_session("q")
    a = node()
    await submit(store, session_id, [a])
    for number in (1, 2):
        started = await start(store, session_id, a["node_id"], max_attempts=2, idempotent=True)
        assert started.attempt.attempt_number == number
        await store.mark_dispatched(started.run.run_id, started.attempt.attempt_id, "running")
        await store.fail_attempt(started.run.run_id, started.attempt.attempt_id, {"code": "error"})
    await refused("MAX_ATTEMPTS", start(store, session_id, a["node_id"], max_attempts=2))
    assert (await store.get_graph(session_id)).activity == "blocked"


async def an_expectation_holds_the_next_node_until_released(store: GraphStore) -> None:
    session_id = await store.create_session("q")
    a = node(expect={"v": 1, "rules": [{"metric": "n", "op": ">=", "value": 10}]})
    b = node()
    await submit(store, session_id, [a, b])
    started = await start(store, session_id, a["node_id"])
    await run_ok(store, started, {"n": 5})
    pin = {"in": {"node_id": a["node_id"], "run_id": started.run.run_id, "port": "summary"}}
    await refused("NOT_READY", start(store, session_id, b["node_id"], pins=pin))
    released = await submit(store, session_id, [a, b], release_hold_node_ids=[a["node_id"]])
    assert released.change.status == "applied"
    await start(store, session_id, b["node_id"], pins=pin)


async def a_cancel_is_confirmed_and_never_retried_automatically(store: GraphStore) -> None:
    session_id = await store.create_session("q")
    a = node()
    await submit(store, session_id, [a])
    started = await start(store, session_id, a["node_id"], max_attempts=3)
    run_id, attempt_id = started.run.run_id, started.attempt.attempt_id
    await store.mark_dispatched(run_id, attempt_id, "running")
    await refused("NO_CANCEL_REQUEST", store.confirm_stopped(run_id, attempt_id))
    requested = await store.request_cancel(run_id, attempt_id, "timeout")
    assert requested.state == "running" and requested.cancel_requested_at is not None
    assert (await store.confirm_stopped(run_id, attempt_id)).state == "failed"  # a timeout fails
    retry = await start(store, session_id, a["node_id"], max_attempts=3)
    await store.mark_dispatched(retry.run.run_id, retry.attempt.attempt_id, "running")
    await store.request_cancel(retry.run.run_id, retry.attempt.attempt_id, "user")
    assert (await store.confirm_stopped(retry.run.run_id, retry.attempt.attempt_id)).state == "cancelled"
    await refused("CANCELLED", start(store, session_id, a["node_id"], max_attempts=3))
    await start(store, session_id, a["node_id"], max_attempts=3, explicit=True)


async def a_gate_suspends_and_the_same_attempt_resumes(store: GraphStore) -> None:
    session_id = await store.create_session("q")
    agent = node("study_population_planner_agent", "agent")
    await submit(store, session_id, [agent])
    started = await start(store, session_id, agent["node_id"], prompt="p", resumable=True)
    run_id, attempt_id = started.run.run_id, started.attempt.attempt_id
    await store.mark_dispatched(run_id, attempt_id, "running")
    gate = {"kind": "gate", "gate": "tool_approval", "interrupt_id": "i1", "checkpoint_id": "c1"}
    await refused("REFUSED_BY_DATABASE", store.suspend(run_id, attempt_id, gate))
    await store.put_checkpoint(run_id, attempt_id, **checkpoint("c1"))
    assert (await store.suspend(run_id, attempt_id, gate)).state == "suspended"
    assert (await store.get_graph(session_id)).activity == "awaiting_user"
    await refused("NOT_RESUMABLE", store.resume(run_id, attempt_id))
    attempt, action = await store.reconcile(run_id, attempt_id, "not_found")
    assert (attempt.state, action) == ("suspended", "unchanged")
    await store.gate_decision(run_id, attempt_id, "i1", "approve")
    await store.set_control_state(session_id, "paused")
    await refused("NOT_RESUMABLE", store.resume(run_id, attempt_id))
    await store.set_control_state(session_id, "active")
    resumed = await store.resume(run_id, attempt_id)
    assert (resumed.attempt_id, resumed.state) == (attempt_id, "running")


async def checkpoints_are_fenced_and_seeds_copied_only_when_compatible(store: GraphStore) -> None:
    session_id = await store.create_session("q")
    agent = node("study_population_planner_agent", "agent")
    await submit(store, session_id, [agent])
    started = await start(store, session_id, agent["node_id"], prompt="p", resumable=True, spec="s1")
    run_id, attempt_id = started.run.run_id, started.attempt.attempt_id
    await store.mark_dispatched(run_id, attempt_id, "running")
    await store.put_checkpoint(run_id, attempt_id, **checkpoint("c1"))
    await store.put_checkpoint(run_id, attempt_id, **checkpoint("c1"))  # a replay is a no-op
    await refused("REFUSED_BY_DATABASE", store.put_checkpoint(run_id, str(uuid.uuid4()), **checkpoint("c2")))
    assert (await store.get_checkpoint(run_id)).checkpoint_id == "c1"
    await store.complete_attempt(run_id, attempt_id, {"v": 1, "ports": {}})
    await refused("REFUSED_BY_DATABASE", store.put_checkpoint(run_id, attempt_id, **checkpoint("c3")))
    seeded = {**agent, "seed_from": {"run_id": run_id, "checkpoint_id": "c1"}}
    for spec, applied in (("other", False), ("s1", True)):
        change = await submit(store, session_id, [seeded], rerun_node_ids=[agent["node_id"]])
        await approve(store, change)
        rerun = await start(store, session_id, agent["node_id"], prompt="p", resumable=True, spec=spec)
        assert rerun.run.execution_policy["seed_from"]["applied"] is applied
        copied = await store.get_checkpoint(rerun.run.run_id)
        assert (copied is not None) is applied
        await run_ok(store, rerun)


SCENARIOS: list[Callable[[GraphStore], Awaitable[None]]] = [
    auto_change_applies_and_cas_refuses_a_stale_base,
    plan_mode_waits_for_a_decision_by_digest,
    one_attempt_at_a_time_and_readiness,
    a_rerun_supersedes_and_keeps_old_pins,
    a_running_node_is_affected_only_on_restart,
    retries_stop_at_the_attempt_limit,
    an_expectation_holds_the_next_node_until_released,
    a_cancel_is_confirmed_and_never_retried_automatically,
    a_gate_suspends_and_the_same_attempt_resumes,
    checkpoints_are_fenced_and_seeds_copied_only_when_compatible,
]
