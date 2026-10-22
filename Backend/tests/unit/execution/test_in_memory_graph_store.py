"""InMemoryGraphStore rules the shared scenario table cannot drive through
the public protocol: the deployment's project key and the retention purge."""

from __future__ import annotations

import pytest

from agent_system.execution.in_memory_graph_store import InMemoryGraphStore
from tests.support.graph_store_scenarios import (
    PROJECT_KEY,
    approve,
    entries,
    refused,
    run_ok,
    running_agent,
    start,
    submit,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_a_transcript_from_another_deployment_is_refused():
    store = InMemoryGraphStore(project_key=PROJECT_KEY)
    _, _, started = await running_agent(store)
    await refused("PROJECT_KEY_MISMATCH", store.append_transcript(
        started.run.run_id, started.attempt.attempt_id, entries("m1"), project_key="other-host"))
    assert await store.append_transcript(
        started.run.run_id, started.attempt.attempt_id, entries("m1"), project_key=PROJECT_KEY) == 1


async def test_the_purge_keeps_a_transcript_a_live_seed_names():
    store = InMemoryGraphStore(transcript_retention_days=0)
    session_id, agent, first = await running_agent(store)
    run_id = first.run.run_id
    await store.append_transcript(run_id, first.attempt.attempt_id, entries("m1", "m2"), project_key=PROJECT_KEY)
    await run_ok(store, first)
    # A pending rerun seeded from m1 keeps the source transcript alive.
    seeded = {**agent, "seed_from": {"run_id": run_id, "message_uuid": "m1"}}
    change = await submit(store, session_id, [seeded], rerun_node_ids=[agent["node_id"]])
    assert (await store.purge_transcripts())["runs"] == 0
    await approve(store, change)
    rerun = await start(store, session_id, agent["node_id"], prompt="p", resumable=True)
    await run_ok(store, rerun)
    # Once the seeded run started, nothing names it any more.
    assert await store.purge_transcripts() == {"runs": 1, "entries": 2, "main_sessions": 0}
    assert await store.load_transcript(run_id) is None


async def test_the_purge_drops_the_main_transcript_of_an_archived_session():
    store = InMemoryGraphStore(transcript_retention_days=0)
    session_id = await store.create_session("q")
    await store.append_main_transcript(session_id, entries("u1"), project_key=PROJECT_KEY)
    assert (await store.purge_transcripts())["main_sessions"] == 0
    await store.set_control_state(session_id, "archived")
    assert (await store.purge_transcripts())["main_sessions"] == 1
    assert await store.load_main_transcript(session_id) is None
