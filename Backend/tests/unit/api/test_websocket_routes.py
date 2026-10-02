"""/ws requires a UUID session_id at the handshake and closes with 1008
before accepting otherwise (T2, WEBSOCKET_FIX_PLAN.md W1/WS-02) -- a stale or
misconfigured client must fail loudly, not connect and silently receive
nothing. It also runs the handshake-cursor replay (WS-08) before the client
can send anything.

The real socket_store.manager is a process-wide singleton; these tests swap
in a throwaway ConnectionManager per test so nothing here can leak state
into (or pick up state from) any other test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.routes import websocket as websocket_routes
from api.routes import sessions as sessions_routes
from socket_store import ConnectionManager

pytestmark = pytest.mark.unit

VALID_SESSION_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def isolated_manager(monkeypatch):
    manager = ConnectionManager()
    monkeypatch.setattr(websocket_routes, "ws_manager", manager)
    return manager


@pytest.fixture(autouse=True)
def stub_handshake_catchup(monkeypatch):
    """The handshake now replays from the durable log and reconstructs a
    segmentation snapshot before the connect handler returns (WS-08) -- ws
    tests must not touch the network or a database (CLAUDE.md), so both are
    stubbed to their "nothing to replay" shape by default. Tests that care
    about replay's own behavior belong in test_socket_store_routing.py,
    which exercises ConnectionManager.replay directly.
    """
    import socket_store

    monkeypatch.setattr(
        socket_store.BackendClient, "replay_events", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        sessions_routes.population_review, "current_batch", AsyncMock(return_value=None)
    )


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(websocket_routes.router)
    with TestClient(app) as test_client:
        yield test_client


def test_missing_session_id_is_rejected_before_accept(client, isolated_manager):
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws"):
            pass
    assert excinfo.value.code == 1008
    assert isolated_manager.session_connections == {}


@pytest.mark.parametrize("session_id", ["not-a-uuid", "", "12345"])
def test_non_uuid_session_id_is_rejected_before_accept(client, isolated_manager, session_id):
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/ws?session_id={session_id}"):
            pass
    assert excinfo.value.code == 1008
    assert isolated_manager.session_connections == {}


def test_a_valid_session_id_is_accepted_and_bound(client, isolated_manager):
    with client.websocket_connect(f"/ws?session_id={VALID_SESSION_ID}"):
        assert VALID_SESSION_ID in isolated_manager.session_connections
        assert len(isolated_manager.session_connections[VALID_SESSION_ID]) == 1

    # The handler's finally block must disconnect regardless of how the
    # socket went away (W6) -- here, the client closing first.
    assert VALID_SESSION_ID not in isolated_manager.session_connections


def test_inbound_frames_are_ignored_the_socket_is_push_only(client, isolated_manager):
    """WS-10: client input moved to REST (POST /session/{id}/input); /ws
    no longer reads or acts on anything the client sends -- not even a
    well-formed `user` frame that used to reach the planner's input queue.
    Sending frames must not create a queue at all (also still covers W5's
    "default" orphan queue, since there is no per-message routing left to
    misroute)."""
    with client.websocket_connect(f"/ws?session_id={VALID_SESSION_ID}") as ws:
        ws.send_text("not json")
        ws.send_text('{"type": "user", "payload": {"text": "hello"}}')

    assert isolated_manager.message_queues == {}


# ----------------------------------------------------------------------
# WS-08 — handshake-cursor replay
# ----------------------------------------------------------------------


def test_after_event_id_reaches_replay_logs_as_the_cursor(client, isolated_manager, monkeypatch):
    import socket_store

    seen = {}

    async def fake_replay_logs(session_id, after_event_id=0, limit=500):
        seen["after_event_id"] = after_event_id
        return []

    monkeypatch.setattr(socket_store.BackendClient, "replay_events", fake_replay_logs)

    with client.websocket_connect(f"/ws?session_id={VALID_SESSION_ID}&after_event_id=42"):
        pass

    assert seen["after_event_id"] == 42


@pytest.mark.parametrize("after_event_id", ["not-a-number", "-5", ""])
def test_a_malformed_after_event_id_defaults_to_a_full_replay_not_a_rejection(
    client, isolated_manager, monkeypatch, after_event_id
):
    """Unlike session_id, a bad cursor must not lock a stale client out --
    it just means "replay everything" (WS-08)."""
    import socket_store

    seen = {}

    async def fake_replay_logs(session_id, after_event_id=0, limit=500):
        seen["after_event_id"] = after_event_id
        return []

    monkeypatch.setattr(socket_store.BackendClient, "replay_events", fake_replay_logs)

    with client.websocket_connect(
        f"/ws?session_id={VALID_SESSION_ID}&after_event_id={after_event_id}"
    ):
        assert VALID_SESSION_ID in isolated_manager.session_connections

    assert seen["after_event_id"] == 0


def test_a_replay_failure_closes_with_the_replay_failed_code(client, isolated_manager, monkeypatch):
    """W4: a durable-log read failure at handshake time must close the
    socket, not connect and silently deliver nothing."""
    import socket_store
    from socket_store import WS_CLOSE_REPLAY_FAILED

    async def failing_replay_logs(session_id, after_event_id=0, limit=500):
        raise RuntimeError("application_api unreachable")

    monkeypatch.setattr(socket_store.BackendClient, "replay_events", failing_replay_logs)

    # accept() already happened (unlike the 1008 case) before the failure,
    # so the client only observes the close once it tries to read: first the
    # "replay_failed" frame the manager sends, then the close itself.
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/ws?session_id={VALID_SESSION_ID}") as ws:
            ws.receive_json()
            ws.receive_text()
    assert excinfo.value.code == WS_CLOSE_REPLAY_FAILED
    assert VALID_SESSION_ID not in isolated_manager.session_connections
