"""/events and /ws-broadcast reject unauthenticated requests (T8,
WEBSOCKET_FIX_PLAN.md W12/WS-01).

The router's WS_INTERNAL_TOKEN dependency runs before either handler body, so
the missing/wrong-token cases below never touch BackendClient or
session_service -- nothing to mock. The accepted-token cases do reach the
handler, so BackendClient.save_event and session_service.persist_agent_status
are patched out; this suite must not touch the network or a database.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import events as events_routes

pytestmark = pytest.mark.unit


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(events_routes.router)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def stub_side_effects(monkeypatch):
    """Stand in for the network/DB calls a successfully-authenticated request
    would trigger, so the 200-path tests stay deterministic and offline.

    receive_tool_event (the /events handler for a plain ToolEvent) does a
    deferred `from app import session_service` and falls back to the DB when
    a session isn't already in memory -- importing all of app.py and hitting
    the network is unrelated to what WS-01 changes, so it is stubbed at the
    events_routes module boundary rather than followed deeper.
    """
    receive_tool_event = AsyncMock(return_value=None)
    persist_status = AsyncMock(return_value=None)
    monkeypatch.setattr(events_routes, "receive_tool_event", receive_tool_event)
    # /events' ToolEvent | latex_output union can match either model for a
    # minimal body (extra fields are ignored, not rejected) -- patch both of
    # the branch's possible side effects rather than depend on which one
    # Pydantic's union resolution happens to pick.
    monkeypatch.setattr(events_routes, "send_ws_message", AsyncMock(return_value=None))
    monkeypatch.setattr(
        events_routes.ws_manager, "send_ws_message", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        events_routes.session_service, "persist_agent_status", persist_status
    )
    return {"receive_tool_event": receive_tool_event, "persist_status": persist_status}


EVENTS_BODY = {
    "event_type": "run_started",
    "source": "test",
    "content_type": "text",
    "content": {"message": "hello"},
    "session_id": "11111111-1111-1111-1111-111111111111",
}
BROADCAST_BODY = {
    "type": "tool_progress",
    "payload": {"current": 1, "total": 2},
    "session_id": "11111111-1111-1111-1111-111111111111",
}


@pytest.mark.parametrize("path,body", [("/events", EVENTS_BODY), ("/ws-broadcast", BROADCAST_BODY)])
def test_rejects_a_request_with_no_token_header(client, monkeypatch, path, body):
    monkeypatch.setenv("WS_INTERNAL_TOKEN", "a-real-secret")

    response = client.post(path, json=body)

    assert response.status_code == 401


@pytest.mark.parametrize("path,body", [("/events", EVENTS_BODY), ("/ws-broadcast", BROADCAST_BODY)])
def test_rejects_a_request_with_the_wrong_token(client, monkeypatch, path, body):
    monkeypatch.setenv("WS_INTERNAL_TOKEN", "a-real-secret")

    response = client.post(path, json=body, headers={"X-Internal-Token": "not-it"})

    assert response.status_code == 401


@pytest.mark.parametrize("path,body", [("/events", EVENTS_BODY), ("/ws-broadcast", BROADCAST_BODY)])
def test_accepts_a_request_with_the_configured_token(client, monkeypatch, path, body):
    monkeypatch.setenv("WS_INTERNAL_TOKEN", "a-real-secret")

    response = client.post(path, json=body, headers={"X-Internal-Token": "a-real-secret"})

    assert response.status_code == 200


def test_ws_broadcast_writes_durable_agent_status_only_once_authenticated(
    client, monkeypatch, stub_side_effects
):
    """The vulnerability W12 flags isn't just event injection: an
    unauthenticated caller could also overwrite durable agent status via
    agent_waiting_for_user_input. Confirm that path is gated too."""
    monkeypatch.setenv("WS_INTERNAL_TOKEN", "a-real-secret")
    body = {
        "type": "agent_waiting_for_user_input",
        "payload": {"agent": "planner", "status": "waiting"},
        "session_id": "11111111-1111-1111-1111-111111111111",
    }

    path = "/ws-broadcast"
    rejected = client.post(path, json=body)
    assert rejected.status_code == 401
    stub_side_effects["persist_status"].assert_not_called()

    accepted = client.post(
        path, json=body, headers={"X-Internal-Token": "a-real-secret"}
    )
    assert accepted.status_code == 200
    stub_side_effects["persist_status"].assert_awaited_once_with(
        "11111111-1111-1111-1111-111111111111", "planner", "waiting"
    )
