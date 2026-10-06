"""Route validation of the harness routes, with fake
repositories: no database. What a route itself decides -- the write-path headers, the
request shapes, the retired plan API -- is checked here; the transactions are
checked live in tests/integration/test_v1_p_transactions_live.py."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

httpx = pytest.importorskip("httpx")
pytest_asyncio = pytest.importorskip("pytest_asyncio")

USER = uuid.uuid4()


@pytest_asyncio.fixture
async def client(monkeypatch):
    from application_api.api import dependencies
    from application_api.db.session import get_db
    from application_api.main import app

    async def no_db():
        yield object()

    async def owner():
        return USER

    async def owns():
        return uuid.uuid4()

    app.dependency_overrides[get_db] = no_db
    app.dependency_overrides[dependencies.current_user] = owner
    app.dependency_overrides[dependencies.owned_run] = owns
    app.dependency_overrides[dependencies.owned_session] = lambda session_id: session_id
    app.dependency_overrides[dependencies.owned_change] = owns
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as http:
        yield http
    app.dependency_overrides.clear()


def _attempt(run_id, attempt_id) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {"attempt_id": str(attempt_id), "run_id": str(run_id), "session_id": str(uuid.uuid4()),
            "attempt_number": 1, "dispatch_kind": "in_process", "state": "succeeded", "started_at": now,
            "ended_at": now}


OUTPUTS = {"outputs": {"v": 1, "ports": {}}}


async def test_an_attempt_completes_only_itself(client, monkeypatch):
    from application_api.repositories import run_attempts

    calls = []

    async def complete(db, run_id, attempt_id, data):
        calls.append(attempt_id)
        return _attempt(run_id, attempt_id)

    monkeypatch.setattr(run_attempts, "complete_attempt", complete)
    run_id, attempt_id = uuid.uuid4(), uuid.uuid4()
    url = f"/runs/{run_id}/attempts/{attempt_id}/complete"
    assert (await client.post(url, json=OUTPUTS)).status_code == 403
    other = await client.post(url, json=OUTPUTS, headers={"X-Attempt-Id": str(uuid.uuid4())})
    assert other.status_code == 403
    ok = await client.post(url, json=OUTPUTS, headers={"X-Attempt-Id": str(attempt_id)})
    assert ok.status_code == 200 and calls == [attempt_id]


async def test_a_write_declares_one_path(client):
    response = await client.post(
        f"/runs/{uuid.uuid4()}/scratchpad/notes", json={"content": "x"},
        headers={"X-Attempt-Id": str(uuid.uuid4()), "X-Run-Id": str(uuid.uuid4())},
    )
    assert response.status_code == 422


async def test_a_checkpoint_write_presents_its_attempt(client):
    body = {"checkpoint_id": "c", "type": "t", "checkpoint": "AA==",
            "metadata": {"v": 1, "spec_digest": "s", "engine_version": "1"}}
    assert (await client.put(f"/runs/{uuid.uuid4()}/checkpoints", json=body)).status_code == 403
    bad = {**body, "metadata": {"v": 1}}
    response = await client.put(f"/runs/{uuid.uuid4()}/checkpoints", json=bad,
                                headers={"X-Attempt-Id": str(uuid.uuid4())})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"author": "user", "base_version": 0, "content": {"v": 1, "nodes": []}, "approval_reasons": ["because"]},
        {"author": "nobody", "base_version": 0, "content": {"v": 1, "nodes": []}},
        {"author": "user", "base_version": -1, "content": {"v": 1, "nodes": []}},
        {"author": "user", "base_version": 0, "content": {"v": 1, "nodes": [{"node_id": "n"}]}},
    ],
)
async def test_change_requests_are_validated(client, body):
    response = await client.post(f"/session-graphs/session/{uuid.uuid4()}/changes", json=body,
                                 headers={"Idempotency-Key": "k"})
    assert response.status_code == 422


async def test_the_main_names_its_tool_call_and_users_send_a_key(client):
    url = f"/session-graphs/session/{uuid.uuid4()}/changes"
    body = {"author": "main", "base_version": 0, "content": {"v": 1, "nodes": []}}
    response = await client.post(url, json=body)
    assert response.json()["detail"]["code"] == "TOOL_CALL_REQUIRED"
    response = await client.post(url, json={**body, "author": "user"})
    assert response.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


async def test_a_decision_quotes_a_digest(client):
    response = await client.post(f"/graph-changes/{uuid.uuid4()}/decision",
                                 json={"decision": "approve", "content_digest": "abc"})
    assert response.status_code == 422


async def test_the_draft_plan_api_is_gone(client):
    session_id = uuid.uuid4()
    assert (await client.post(f"/session-graphs/session/{session_id}/approve")).status_code == 410
    assert (await client.put(f"/session-graphs/session/{session_id}/nodes", json={})).status_code == 410


async def test_start_requires_a_known_dispatch_kind(client):
    body = {"node_id": str(uuid.uuid4()), "accepted_by_change_id": str(uuid.uuid4()), "dispatch_kind": "thread"}
    assert (await client.post("/runs/start", json=body)).status_code == 422
