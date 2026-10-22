"""HarnessClient: the write-path header on an attempt's own
writes, the idempotency key, and refusals as stable codes. No network."""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

httpx = pytest.importorskip("httpx")
respx = pytest.importorskip("respx")

from agent_system.clients.harness_client import ApplicationApiError, HarnessClient  # noqa: E402

BASE = "http://api.test"


@pytest.fixture
def api():
    with respx.mock(base_url=BASE, assert_all_called=True) as router:
        yield router


async def test_an_attempts_own_writes_carry_its_id(api):
    complete = api.post("/runs/r/attempts/a/complete").respond(200, json={"state": "succeeded"})
    dispatched = api.post("/runs/r/attempts/a/dispatched").respond(200, json={"state": "running"})
    async with HarnessClient(BASE) as client:
        await client.complete_attempt("r", "a", {"v": 1, "ports": {}})
        await client.mark_dispatched("r", "a", "running")
    assert complete.calls.last.request.headers["X-Attempt-Id"] == "a"
    assert "X-Attempt-Id" not in dispatched.calls.last.request.headers  # the harness's transition


async def test_a_user_change_sends_its_idempotency_key(api):
    route = api.post("/session-graphs/session/s/changes").respond(200, json={"version": 1})
    async with HarnessClient(BASE) as client:
        await client.submit_change("s", author="user", base_version=0, content={"v": 1, "nodes": []},
                                   idempotency_key="k1")
    assert route.calls.last.request.headers["Idempotency-Key"] == "k1"


async def test_refusals_keep_their_code_and_hide_server_errors(api):
    api.post("/runs/start").respond(409, json={"detail": {"code": "NOT_READY", "message": "wait"}})
    api.get("/runs/x").respond(500, text="Traceback ... SQL ...")
    async with HarnessClient(BASE) as client:
        with pytest.raises(ApplicationApiError) as refused:
            await client.start_node({})
        assert (refused.value.status_code, refused.value.code) == (409, "NOT_READY")
        with pytest.raises(ApplicationApiError) as failed:
            await client.get_run("x")
        assert failed.value.code == "INTERNAL" and "Traceback" not in failed.value.message


async def test_an_attempts_transcript_and_pending_tools_present_the_attempt(api):
    append = api.post("/runs/r/transcript").respond(200, json={"stored": 1, "replayed": 0})
    pending = api.put("/runs/r/attempts/a/pending-tools").respond(200, json={"state": "running"})
    read = api.get("/runs/r/transcript").respond(200, json={"subpath": "", "entries": [{"uuid": "m1"}]})
    async with HarnessClient(BASE) as client:
        await client.append_transcript("r", "a", [{"uuid": "m1"}], project_key="cubeai")
        await client.set_pending_tools("r", "a", [])
        assert await client.load_transcript("r", attempt_id="b") == [{"uuid": "m1"}]
    assert append.calls.last.request.headers["X-Attempt-Id"] == "a"
    assert json.loads(append.calls.last.request.content) == {
        "subpath": "", "project_key": "cubeai", "entries": [{"uuid": "m1"}]}
    assert json.loads(pending.calls.last.request.content) == {"v": 1, "calls": []}
    assert read.calls.last.request.headers["X-Attempt-Id"] == "b"  # the reader presents itself


async def test_a_missing_transcript_is_none(api):
    api.get("/runs/r/transcript").respond(404, json={"detail": "none"})
    api.get("/sessions/s/main-transcript").respond(404, json={"detail": "none"})
    async with HarnessClient(BASE) as client:
        assert await client.load_transcript("r") is None
        assert await client.load_main_transcript("s", subpath="subagents/agent-composer") is None


async def test_an_answer_is_the_users_and_carries_only_what_was_given(api):
    route = api.post("/runs/r/attempts/a/answer").respond(200, json={"state": "suspended"})
    async with HarnessClient(BASE) as client:
        await client.answer_attempt("r", "a", "toolu_1", decision="approve")
        await client.answer_attempt("r", "a", "toolu_2", answer=False, comment="no wavelets")
    first, second = (json.loads(c.request.content) for c in route.calls)
    assert first == {"tool_use_id": "toolu_1", "comment": None, "decision": "approve"}
    assert second == {"tool_use_id": "toolu_2", "comment": "no wavelets", "answer": False}
    assert "X-Attempt-Id" not in route.calls.last.request.headers  # the user gate path
