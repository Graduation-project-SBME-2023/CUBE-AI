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


async def submit(store: GraphStore, session_id: str, nodes: list[dict], *, reasons=(), author: str = "user",
                 **content: Any):
    version = (await store.get_graph(session_id)).version
    # The Main's change is keyed by its SDK tool call (D21); anyone else's by an idempotency key.
    call = {"session_id": session_id, "tool_use_id": f"toolu_{uuid.uuid4().hex}"} if author == "main" else None
    return await store.submit_change(
        session_id, author=author, base_version=version, content={"v": 1, "nodes": nodes, **content},
        approval_reasons=tuple(reasons), tool_call=call, idempotency_key=None if call else str(uuid.uuid4()),
    )


async def approve(store: GraphStore, result) -> Any:
    return await store.decide_change(result.change.change_id, "approve", result.change.content_digest)


async def start(store: GraphStore, session_id: str, node_id: str, *, pins: Optional[dict] = None,
                prompt: Optional[str] = None, explicit: bool = False, spec: Optional[str] = None,
                engine: Optional[str] = None, **limits: Any):
    graph = await store.get_graph(session_id)
    live = next(item.node for item in graph.nodes if item.node.node_id == node_id)
    return await store.start_node(StartRequest(
        node_id=node_id, accepted_by_change_id=live.accepted_by_change_id, limits=limits,
        resolved_inputs={"v": 1, "ports": pins or {}}, compiled_prompt=prompt, explicit_retry=explicit,
        spec_digest=spec, engine_version=engine,
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


PROJECT_KEY = "cubeai"


def entries(*uuids: str) -> list[dict[str, Any]]:
    """Opaque SDK transcript entries; only their uuid is read."""
    return [{"uuid": u, "type": "assistant", "message": {"content": f"step {u}"}} for u in uuids]


def call(tool_use_id: str, tool: str, idempotent: bool) -> dict[str, Any]:
    return {"tool_use_id": tool_use_id, "tool": tool, "idempotent": idempotent,
            "started_at": "2026-10-21T10:00:00+00:00"}


async def running_agent(store: GraphStore, **limits: Any):
    """A session with one resumable agent node whose first attempt is running."""
    session_id = await store.create_session("q")
    agent = node("study_population_planner_agent", "agent")
    await submit(store, session_id, [agent])
    started = await start(store, session_id, agent["node_id"], prompt="p", resumable=True, **limits)
    await store.mark_dispatched(started.run.run_id, started.attempt.attempt_id, "running")
    return session_id, agent, started


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
    history = await store.node_runs(a["node_id"])
    assert [run.run_id for run in history] == [first.run.run_id, fresh.run.run_id]
    assert history[0].superseded_at is not None and history[1].superseded_at is None


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


async def a_question_suspends_and_the_same_attempt_resumes(store: GraphStore) -> None:
    session_id, _, started = await running_agent(store)
    run_id, attempt_id = started.run.run_id, started.attempt.attempt_id
    question = {"kind": "ask_user", "tool_use_id": "toolu_1", "payload": {"question": "use wavelets?"}}
    await refused("INVALID", store.suspend(run_id, attempt_id, {"kind": "ask_user"}))  # names no tool call
    suspended = await store.suspend(run_id, attempt_id, question)
    assert (suspended.state, suspended.suspension["v"]) == ("suspended", 2)
    await store.suspend(run_id, attempt_id, question)  # a replay is a no-op
    assert (await store.get_graph(session_id)).activity == "awaiting_user"
    await refused("NOT_RESUMABLE", store.resume(run_id, attempt_id))  # not answered yet
    attempt, action = await store.reconcile(run_id, attempt_id, "not_found")
    assert (attempt.state, action) == ("suspended", "unchanged")  # a restart leaves it waiting
    await refused("NO_OPEN_GATE", store.answer(run_id, attempt_id, "toolu_2", answer="yes"))
    answered = await store.answer(run_id, attempt_id, "toolu_1", decision="approve", comment="fine")
    assert answered.suspension["resume"]["decision"] == "approve"
    await refused("NO_OPEN_GATE", store.answer(run_id, attempt_id, "toolu_1", decision="reject"))  # once
    await store.set_control_state(session_id, "paused")
    await refused("NOT_RESUMABLE", store.resume(run_id, attempt_id))
    await store.set_control_state(session_id, "active")
    resumed = await store.resume(run_id, attempt_id)
    assert (resumed.attempt_id, resumed.state) == (attempt_id, "running")
    # A pause names no tool call, and resumes with no answer.
    await store.suspend(run_id, attempt_id, {"kind": "pause"})
    assert (await store.resume(run_id, attempt_id)).state == "running"
    # A domain form of ask_user is answered by its own route (it writes the form's columns).
    form = {"kind": "ask_user", "tool_use_id": "toolu_3", "form": "cohort_approval"}
    assert (await store.suspend(run_id, attempt_id, form)).suspension["form"] == "cohort_approval"
    await refused("FORM_ANSWER", store.answer(run_id, attempt_id, "toolu_3", decision="approve"))


async def a_tool_approval_takes_a_decision_and_a_cancel_is_confirmed_at_once(store: GraphStore) -> None:
    _, _, started = await running_agent(store)
    run_id, attempt_id = started.run.run_id, started.attempt.attempt_id
    await store.suspend(run_id, attempt_id, {"kind": "tool_approval", "tool_use_id": "toolu_9"})
    await refused("NO_OPEN_GATE", store.answer(run_id, attempt_id, "toolu_9", answer="ok"))  # needs a decision
    await refused("INVALID", store.answer(run_id, attempt_id, "toolu_9", answer="ok", decision="approve"))
    stopped = await store.request_cancel(run_id, attempt_id, "user")
    assert stopped.state == "cancelled"  # a suspended attempt holds no work


async def a_suspend_after_a_cancel_request_is_refused(store: GraphStore) -> None:
    _, _, started = await running_agent(store)
    run_id, attempt_id = started.run.run_id, started.attempt.attempt_id
    await store.request_cancel(run_id, attempt_id, "user")  # the work has not stopped yet
    question = {"kind": "ask_user", "tool_use_id": "toolu_1", "payload": {"question": "use wavelets?"}}
    await refused("CANCEL_REQUESTED", store.suspend(run_id, attempt_id, question))
    await refused("CANCEL_REQUESTED", store.suspend(run_id, attempt_id, {"kind": "pause"}))
    assert (await store.confirm_stopped(run_id, attempt_id)).state == "cancelled"


async def an_ended_attempt_records_its_usage(store: GraphStore) -> None:
    session_id, _, done = await running_agent(store)
    outputs = {"v": 1, "ports": {"summary": {
        "type": "ResultSummary", "ref": {"table": "node_runs", "ids": [done.run.run_id]}, "summary": {},
    }}}
    completed = await store.complete_attempt(done.run.run_id, done.attempt.attempt_id, outputs,
                                             {"v": 1, "input_tokens": 1200, "output_tokens": 300})
    assert (completed.usage["input_tokens"], completed.usage["output_tokens"]) == (1200, 300)
    _, _, stopped = await running_agent(store)
    await store.request_cancel(stopped.run.run_id, stopped.attempt.attempt_id, "user")
    confirmed = await store.confirm_stopped(stopped.run.run_id, stopped.attempt.attempt_id,
                                            {"v": 1, "input_tokens": 40, "output_tokens": 2})
    assert (confirmed.state, confirmed.usage["input_tokens"]) == ("cancelled", 40)
    _, _, failed = await running_agent(store)
    ended = await store.fail_attempt(failed.run.run_id, failed.attempt.attempt_id, {"code": "error"})
    assert ended.usage is None  # nothing reported, nothing stored


async def step(store: GraphStore, session_id: str) -> Optional[tuple[str, str]]:
    """The graph's next step as (action, node_id), or None."""
    view = await store.get_graph(session_id)
    return (view.next_step.action, view.next_step.node_id) if view.next_step else None


async def the_next_step_follows_the_graph(store: GraphStore) -> None:
    session_id = await store.create_session("q")
    assert await step(store, session_id) is None  # an empty graph
    a, b = node(), node()
    await submit(store, session_id, [a, b])
    assert await step(store, session_id) == ("start", a["node_id"])
    first = await start(store, session_id, a["node_id"])
    assert await step(store, session_id) is None  # one attempt at a time
    await run_ok(store, first)
    assert await step(store, session_id) == ("start", b["node_id"])
    await store.set_control_state(session_id, "paused")
    assert await step(store, session_id) is None  # a paused session starts nothing
    await store.set_control_state(session_id, "active")
    pin = {"in": {"node_id": a["node_id"], "run_id": first.run.run_id, "port": "summary"}}
    second = await start(store, session_id, b["node_id"], pins=pin)
    await store.mark_dispatched(second.run.run_id, second.attempt.attempt_id, "running")
    await store.fail_attempt(second.run.run_id, second.attempt.attempt_id, {"code": "error"})
    assert await step(store, session_id) is None  # a failed run waits for a retry decision
    # A change that reruns a running node: its attempt is still stopping, so nothing starts.
    session_id = await store.create_session("q")
    c = node()
    await submit(store, session_id, [c])
    third = await start(store, session_id, c["node_id"])
    await store.mark_dispatched(third.run.run_id, third.attempt.attempt_id, "running")
    await submit(store, session_id, [{**c, "params": {"x": 1}}], on_running="restart")
    assert await step(store, session_id) is None
    await store.confirm_stopped(third.run.run_id, third.attempt.attempt_id)
    assert await step(store, session_id) == ("start", c["node_id"])


async def the_next_step_resumes_an_answered_or_unpaused_attempt(store: GraphStore) -> None:
    session_id, agent, started = await running_agent(store)
    run_id, attempt_id = started.run.run_id, started.attempt.attempt_id
    await store.suspend(run_id, attempt_id, {"kind": "ask_user", "tool_use_id": "toolu_1"})
    assert await step(store, session_id) is None  # waits for the answer
    await store.answer(run_id, attempt_id, "toolu_1", answer="yes")
    assert await step(store, session_id) == ("resume", agent["node_id"])
    await store.resume(run_id, attempt_id)
    await store.suspend(run_id, attempt_id, {"kind": "pause"})
    await store.set_control_state(session_id, "paused")
    assert await step(store, session_id) is None  # the pause has not ended
    await store.set_control_state(session_id, "active")
    assert await step(store, session_id) == ("resume", agent["node_id"])


async def the_next_step_waits_for_a_release_and_for_a_hold(store: GraphStore) -> None:
    session_id = await store.create_session("q")
    a = node(approval="before", expect={"v": 1, "rules": [{"metric": "n", "op": ">=", "value": 10}]})
    b = node()
    await submit(store, session_id, [a, b])
    assert await step(store, session_id) == ("await_release", a["node_id"])
    await submit(store, session_id, [a, b], release_hold_node_ids=[a["node_id"]])
    assert await step(store, session_id) == ("start", a["node_id"])
    first = await start(store, session_id, a["node_id"])
    await run_ok(store, first, {"n": 5})
    assert await step(store, session_id) is None  # the violated expectation holds the next node
    await submit(store, session_id, [a, b], release_hold_node_ids=[a["node_id"]])
    assert await step(store, session_id) == ("start", b["node_id"])


async def transcripts_are_fenced_and_seeds_checked(store: GraphStore) -> None:
    session_id, agent, first = await running_agent(store, spec="s1", engine="e1", max_attempts=2,
                                                    idempotent=True)
    run_id, a1 = first.run.run_id, first.attempt.attempt_id
    assert await store.load_transcript(run_id) is None
    assert await store.append_transcript(run_id, a1, entries("m1", "m2"), project_key=PROJECT_KEY) == 2
    assert await store.append_transcript(run_id, a1, entries("m2", "m3"), project_key=PROJECT_KEY) == 1
    await store.append_transcript(run_id, a1, entries("x1"), project_key=PROJECT_KEY, subpath="subagents/agent-x")
    await refused("REFUSED_BY_DATABASE", store.append_transcript(
        run_id, str(uuid.uuid4()), entries("m4"), project_key=PROJECT_KEY))  # not this run's attempt
    await refused("INVALID", store.append_transcript(run_id, a1, [{"type": "user"}], project_key=PROJECT_KEY))
    assert [e["uuid"] for e in await store.load_transcript(run_id)] == ["m1", "m2", "m3"]
    assert await store.list_transcript_subpaths(run_id) == ["subagents/agent-x"]
    await store.fail_attempt(run_id, a1, {"code": "error"})
    await refused("REFUSED_BY_DATABASE", store.append_transcript(
        run_id, a1, entries("late"), project_key=PROJECT_KEY))  # a terminal attempt
    second = await start(store, session_id, agent["node_id"], prompt="p")
    assert second.attempt.attempt_number == 2
    assert len(await store.load_transcript(run_id, attempt_id=second.attempt.attempt_id)) == 3  # same session
    await run_ok(store, second)
    # A rerun from message m2: the seed is checked at the first start, never copied.
    seeded = {**agent, "seed_from": {"run_id": run_id, "message_uuid": "m2"}}
    for spec, applied in (("other", False), ("s1", True)):
        change = await submit(store, session_id, [seeded], rerun_node_ids=[agent["node_id"]])
        await approve(store, change)
        rerun = await start(store, session_id, agent["node_id"], prompt="p", resumable=True, spec=spec, engine="e1")
        assert rerun.run.execution_policy["seed_from"]["applied"] is applied
        assert await store.load_transcript(rerun.run.run_id) is None
        if applied:  # the seeded run's first attempt reads its seed
            assert len(await store.load_transcript(run_id, attempt_id=rerun.attempt.attempt_id)) == 3
        await run_ok(store, rerun)
    _, _, other = await running_agent(store)
    await refused("TRANSCRIPT_FORBIDDEN", store.load_transcript(run_id, attempt_id=other.attempt.attempt_id))


async def pending_tools_decide_the_automatic_retry(store: GraphStore) -> None:
    session_id, agent, first = await running_agent(store, max_attempts=4)
    run_id, a1 = first.run.run_id, first.attempt.attempt_id
    await store.set_pending_tools(run_id, a1, [call("toolu_1", "count_studies", True)])
    await refused("INVALID", store.set_pending_tools(run_id, a1, [{"tool_use_id": "toolu_2"}]))
    await refused("NOT_FOUND", store.set_pending_tools(run_id, str(uuid.uuid4()), []))
    await store.reconcile(run_id, a1, "process_gone")  # restart while an idempotent call ran
    second = await start(store, session_id, agent["node_id"], prompt="p")  # retried automatically
    a2 = second.attempt.attempt_id
    await store.mark_dispatched(run_id, a2, "running")
    await store.set_pending_tools(run_id, a2, [call("toolu_3", "save_cohort", False)])
    latest = (await store.node_runs(agent["node_id"]))[0].attempts[-1]
    assert latest.pending_tools["calls"][0]["tool"] == "save_cohort"
    await store.reconcile(run_id, a2, "process_gone")  # restart while a write ran
    await refused("NEEDS_DECISION", start(store, session_id, agent["node_id"], prompt="p"))
    third = await start(store, session_id, agent["node_id"], prompt="p", explicit=True)
    await store.mark_dispatched(run_id, third.attempt.attempt_id, "running")
    await store.fail_attempt(run_id, third.attempt.attempt_id, {"code": "transcript_write_failed"})
    await refused("NEEDS_DECISION", start(store, session_id, agent["node_id"], prompt="p"))
    assert (await store.get_graph(session_id)).activity == "blocked"


async def approval_before_waits_for_the_users_release(store: GraphStore) -> None:
    session_id = await store.create_session("q")
    a = node(approval="before")
    await submit(store, session_id, [a])
    graph = await store.get_graph(session_id)
    assert (graph.nodes[0].node.approval, graph.activity) == ("before", "awaiting_user")
    await refused("NEEDS_RELEASE", start(store, session_id, a["node_id"]))
    await refused("USER_RELEASE_REQUIRED", submit(
        store, session_id, [a], author="main", release_hold_node_ids=[a["node_id"]]))
    released = await submit(store, session_id, [a], release_hold_node_ids=[a["node_id"]])
    graph = await store.get_graph(session_id)
    assert graph.nodes[0].node.start_released_by_change_id == released.change.change_id
    assert graph.activity == "idle"
    edited = {**a, "params": {"threshold": 0.5}}
    await submit(store, session_id, [edited])  # an edit needs a new release
    await refused("NEEDS_RELEASE", start(store, session_id, a["node_id"]))
    await submit(store, session_id, [edited], release_hold_node_ids=[a["node_id"]])
    await start(store, session_id, a["node_id"])


async def approval_after_holds_the_next_node_for_a_user_review(store: GraphStore) -> None:
    session_id = await store.create_session("q")
    a, b = node(approval="after"), node()
    await submit(store, session_id, [a, b])
    started = await start(store, session_id, a["node_id"])
    await run_ok(store, started)
    graph = await store.get_graph(session_id)
    assert (graph.nodes[0].run.downstream_hold, graph.activity) == ("user_review", "awaiting_user")
    pin = {"in": {"node_id": a["node_id"], "run_id": started.run.run_id, "port": "summary"}}
    await refused("NOT_READY", start(store, session_id, b["node_id"], pins=pin))
    await refused("USER_RELEASE_REQUIRED", submit(
        store, session_id, [a, b], author="main", release_hold_node_ids=[a["node_id"]]))
    await submit(store, session_id, [a, b], release_hold_node_ids=[a["node_id"]])
    await start(store, session_id, b["node_id"], pins=pin)


async def the_mains_session_and_its_change_key(store: GraphStore) -> None:
    session_id = await store.create_session("q")
    assert await store.load_main_transcript(session_id) is None
    assert await store.append_main_transcript(session_id, entries("u1", "a1"), project_key=PROJECT_KEY) == 2
    assert await store.append_main_transcript(session_id, entries("a1"), project_key=PROJECT_KEY) == 0
    await store.append_main_transcript(session_id, entries("c1"), project_key=PROJECT_KEY,
                                       subpath="subagents/agent-composer")
    assert [e["uuid"] for e in await store.load_main_transcript(session_id)] == ["u1", "a1"]
    assert await store.list_main_transcript_subpaths(session_id) == ["subagents/agent-composer"]
    # A replayed Main call (same tool_use_id) returns the change it already made.
    tool_call = {"session_id": session_id, "tool_use_id": "toolu_main_1"}
    content = {"v": 1, "nodes": [node()]}
    first = await store.submit_change(session_id, author="main", base_version=0, content=content,
                                      tool_call=tool_call)
    again = await store.submit_change(session_id, author="main", base_version=0, content=content,
                                      tool_call=tool_call)
    assert again.replayed and again.change.change_id == first.change.change_id


SCENARIOS: list[Callable[[GraphStore], Awaitable[None]]] = [
    auto_change_applies_and_cas_refuses_a_stale_base,
    plan_mode_waits_for_a_decision_by_digest,
    one_attempt_at_a_time_and_readiness,
    a_rerun_supersedes_and_keeps_old_pins,
    a_running_node_is_affected_only_on_restart,
    retries_stop_at_the_attempt_limit,
    an_expectation_holds_the_next_node_until_released,
    a_cancel_is_confirmed_and_never_retried_automatically,
    a_question_suspends_and_the_same_attempt_resumes,
    a_tool_approval_takes_a_decision_and_a_cancel_is_confirmed_at_once,
    a_suspend_after_a_cancel_request_is_refused,
    an_ended_attempt_records_its_usage,
    the_next_step_follows_the_graph,
    the_next_step_resumes_an_answered_or_unpaused_attempt,
    the_next_step_waits_for_a_release_and_for_a_hold,
    transcripts_are_fenced_and_seeds_checked,
    pending_tools_decide_the_automatic_retry,
    approval_before_waits_for_the_users_release,
    approval_after_holds_the_next_node_for_a_user_review,
    the_mains_session_and_its_change_key,
]
