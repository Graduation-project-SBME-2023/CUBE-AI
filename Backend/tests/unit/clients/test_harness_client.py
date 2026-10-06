"""HarnessClient: the write-path header on an attempt's own
writes, the idempotency key, and refusals as stable codes. No network."""

from __future__ import annotations

import base64

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


async def test_checkpoints_travel_base64(api):
    put = api.put("/runs/r/checkpoints").respond(200, json={})
    api.get("/runs/r/checkpoints/latest").respond(200, json={
        "checkpoint": base64.b64encode(b"blob").decode(), "writes": [{"value": base64.b64encode(b"w").decode()}],
    })
    api.get("/runs/r/checkpoints/gone").respond(404, json={"detail": "none"})
    async with HarnessClient(BASE) as client:
        await client.put_checkpoint("r", "a", checkpoint_id="c", checkpoint=b"blob", type="t", metadata={})
        latest = await client.get_checkpoint("r")
        assert await client.get_checkpoint("r", "gone") is None
    assert put.calls.last.request.headers["X-Attempt-Id"] == "a"
    assert (latest["checkpoint"], latest["writes"][0]["value"]) == (b"blob", b"w")
