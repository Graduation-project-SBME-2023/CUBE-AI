"""POST /session/{id}/input (WEBSOCKET_FIX_PLAN.md WS-10): the REST
replacement for what used to arrive as a `user`/`user_input`/`chat_input`
frame over the WebSocket. Exercises the route wiring and its
Idempotency-Key dedup (T13) against a throwaway ConnectionManager, per
CLAUDE.md's testing rules (no DB, no network).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import sessions as sessions_routes
from socket_store import ConnectionManager

pytestmark = pytest.mark.unit

SESSION_ID = "11111111-1111-1111-1111-111111111111"
OTHER_SESSION_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def isolated_manager(monkeypatch):
    manager = ConnectionManager()
    monkeypatch.setattr(sessions_routes, "ws_manager", manager)
    return manager


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(sessions_routes.router)
    return TestClient(app)


def test_input_reaches_only_that_session_s_queue(client, isolated_manager):
    response = client.post(f"/session/{SESSION_ID}/input", json={"text": "hello"})

    assert response.status_code == 200
    assert response.json() == {"session_id": SESSION_ID, "status": "queued"}
    assert isolated_manager.message_queues[SESSION_ID].get_nowait() == "hello"
    assert OTHER_SESSION_ID not in isolated_manager.message_queues


def test_a_retry_with_the_same_idempotency_key_is_delivered_once(client, isolated_manager):
    headers = {"Idempotency-Key": "retry-key-1"}

    first = client.post(f"/session/{SESSION_ID}/input", json={"text": "hello"}, headers=headers)
    second = client.post(f"/session/{SESSION_ID}/input", json={"text": "hello"}, headers=headers)

    assert first.status_code == 200
    assert first.json()["status"] == "queued"
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"

    queue = isolated_manager.message_queues[SESSION_ID]
    assert queue.qsize() == 1


def test_a_different_idempotency_key_is_a_second_delivery(client, isolated_manager):
    client.post(
        f"/session/{SESSION_ID}/input", json={"text": "first"}, headers={"Idempotency-Key": "key-a"}
    )
    client.post(
        f"/session/{SESSION_ID}/input", json={"text": "second"}, headers={"Idempotency-Key": "key-b"}
    )

    assert isolated_manager.message_queues[SESSION_ID].qsize() == 2


def test_no_idempotency_key_means_no_dedup(client, isolated_manager):
    """Same as the pre-WS-10 behavior: without a key there is nothing to
    dedup against, so every call queues."""
    client.post(f"/session/{SESSION_ID}/input", json={"text": "hello"})
    client.post(f"/session/{SESSION_ID}/input", json={"text": "hello"})

    assert isolated_manager.message_queues[SESSION_ID].qsize() == 2


def test_an_idempotency_key_is_scoped_to_its_own_session(client, isolated_manager):
    headers = {"Idempotency-Key": "shared-key"}
    client.post(f"/session/{SESSION_ID}/input", json={"text": "a"}, headers=headers)
    client.post(f"/session/{OTHER_SESSION_ID}/input", json={"text": "b"}, headers=headers)

    assert isolated_manager.message_queues[SESSION_ID].qsize() == 1
    assert isolated_manager.message_queues[OTHER_SESSION_ID].qsize() == 1


def test_empty_text_is_rejected(client, isolated_manager):
    response = client.post(f"/session/{SESSION_ID}/input", json={"text": ""})

    assert response.status_code == 422
    assert isolated_manager.message_queues == {}
