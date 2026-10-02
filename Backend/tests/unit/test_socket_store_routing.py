"""ConnectionManager routes events by session_id, never broadcasts to every
socket in the process (T1, WEBSOCKET_FIX_PLAN.md W1/WS-02), stamps the
envelope-v1 fields (WS-13), classifies durable vs. ephemeral before
persisting (WS-05), gives every connection its own outbound queue and sender
task so one stalled socket cannot delay another (T3, WS-04), and replays a
connection's own catch-up window at the handshake without touching any other
socket (T4-T7, WS-08).
"""

from __future__ import annotations

import asyncio
import json

import pytest

import socket_store
from socket_store import ConnectionManager, WS_CLOSE_OVERFLOW
from models.logs import SessionEvent

pytestmark = pytest.mark.unit


class _FakeSocket:
    """A connect()-able stand-in for FastAPI's WebSocket. `accept()` and
    `close()` are no-ops; `send_text()` records the frame and signals
    `received_event` so a test can await delivery deterministically instead
    of guessing how many `asyncio.sleep(0)` calls the sender task needs."""

    def __init__(self):
        self.received: list[dict] = []
        self.received_event = asyncio.Event()
        self.close_codes: list[int] = []

    async def accept(self) -> None:
        pass

    async def send_text(self, message: str) -> None:
        self.received.append(json.loads(message))
        self.received_event.set()

    async def close(self, code: int = 1000) -> None:
        self.close_codes.append(code)


class _StuckSocket(_FakeSocket):
    """send_text() never returns -- simulates a socket that stopped reading.
    `entered` is set the instant the sender task starts sending, so a test
    can wait for exactly that moment instead of racing the event loop."""

    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()

    async def send_text(self, message: str) -> None:
        self.entered.set()
        await asyncio.Event().wait()  # never resolves


async def _wait(event: asyncio.Event, timeout: float = 1.0) -> None:
    await asyncio.wait_for(event.wait(), timeout=timeout)


@pytest.fixture(autouse=True)
def stub_save_log(monkeypatch):
    """These tests exercise routing/envelope shape, not persistence — no DB,
    no network, per CLAUDE.md's testing rules."""
    async def fake_save(log):
        from types import SimpleNamespace
        return SimpleNamespace(event_id=1, payload=log.payload)

    monkeypatch.setattr(socket_store.BackendClient, "save_event", fake_save)


@pytest.mark.asyncio
async def test_an_event_reaches_only_its_own_sessions_sockets():
    manager = ConnectionManager()
    socket_a, socket_b = _FakeSocket(), _FakeSocket()
    await manager.connect(socket_a, "session-a")
    await manager.connect(socket_b, "session-b")

    await manager.send_ws_message("msg", {"n": 1}, session_id="session-a")
    await _wait(socket_a.received_event)

    assert len(socket_a.received) == 1
    assert socket_b.received == []


@pytest.mark.asyncio
async def test_a_session_with_no_connected_socket_is_not_an_error():
    manager = ConnectionManager()

    await manager.send_ws_message("msg", {"n": 1}, session_id="session-a")  # no raise


@pytest.mark.asyncio
async def test_multiple_sockets_on_the_same_session_all_receive_it():
    manager = ConnectionManager()
    socket_1, socket_2 = _FakeSocket(), _FakeSocket()
    await manager.connect(socket_1, "session-a")
    await manager.connect(socket_2, "session-a")

    await manager.send_ws_message("msg", {"n": 1}, session_id="session-a")
    await _wait(socket_1.received_event)
    await _wait(socket_2.received_event)

    assert len(socket_1.received) == 1
    assert len(socket_2.received) == 1


@pytest.mark.asyncio
async def test_envelope_v2_shape():
    manager = ConnectionManager()
    socket = _FakeSocket()
    await manager.connect(socket, "session-a")

    await manager.send_ws_message(
        "msg", {"n": 1}, session_id="session-a", event_id=42
    )
    await _wait(socket.received_event)

    frame = socket.received[0]
    assert frame["v"] == 2
    assert frame["session_id"] == "session-a"
    assert frame["event_id"] == 42
    assert frame["type"] == "msg"
    assert frame["payload"] == {"n": 1}
    assert frame["replaying"] is False
    assert isinstance(frame["ts"], str) and frame["ts"]
    # Nullable now; no emitter supplies these yet (WEBSOCKET_FIX_PLAN.md WS-13).
    assert frame["run_id"] is None
    assert frame["node_id"] is None
    assert frame["run_id"] is None


@pytest.mark.asyncio
async def test_identity_fields_are_carried_through_when_a_caller_supplies_them():
    manager = ConnectionManager()
    socket = _FakeSocket()
    await manager.connect(socket, "session-a")

    await manager.send_ws_message(
        "msg",
        {"n": 1},
        session_id="session-a",
        node_id="node-1",
        run_id="run-1",
        attempt_id="attempt-1",
    )
    await _wait(socket.received_event)

    frame = socket.received[0]
    assert frame["node_id"] == "node-1"
    assert frame["run_id"] == "run-1"
    assert frame["attempt_id"] == "attempt-1"


@pytest.mark.asyncio
async def test_disconnect_removes_the_socket_and_the_empty_session_entry():
    manager = ConnectionManager()
    socket = _FakeSocket()
    await manager.connect(socket, "session-a")

    manager.disconnect(socket)

    assert "session-a" not in manager.session_connections
    assert socket not in manager._connections


@pytest.mark.asyncio
async def test_disconnect_of_an_unregistered_socket_is_a_safe_no_op():
    manager = ConnectionManager()
    manager.disconnect(_FakeSocket())  # no raise, no KeyError


@pytest.mark.asyncio
async def test_disconnect_leaves_a_sibling_socket_on_the_same_session_connected():
    manager = ConnectionManager()
    socket_1, socket_2 = _FakeSocket(), _FakeSocket()
    await manager.connect(socket_1, "session-a")
    await manager.connect(socket_2, "session-a")

    manager.disconnect(socket_1)

    remaining = manager.session_connections["session-a"]
    assert len(remaining) == 1
    assert next(iter(remaining)).websocket is socket_2


# ----------------------------------------------------------------------
# WS-04 — per-connection queue and sender task
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emitting_never_awaits_a_socket():
    """send_ws_message must return even when a connection's own send is
    permanently stuck -- fan-out is put_nowait only (principle 3)."""
    manager = ConnectionManager()
    stuck = _StuckSocket()
    await manager.connect(stuck, "session-a")

    await asyncio.wait_for(
        manager.send_ws_message("msg", {"n": 1}, session_id="session-a"),
        timeout=1.0,
    )  # would time out if send_ws_message awaited the stuck socket


@pytest.mark.asyncio
async def test_a_stalled_sockets_backlog_overflow_evicts_it_without_affecting_others():
    manager = ConnectionManager(queue_maxsize=1)
    stuck = _StuckSocket()
    fast = _FakeSocket()
    await manager.connect(stuck, "session-a")
    await manager.connect(fast, "session-b")

    # Get the stalled connection's sender task actually inside send_text()
    # (queue now empty, one frame "in flight" forever) before piling up a
    # backlog behind it -- otherwise the timing of when overflow happens
    # would depend on event-loop scheduling.
    await manager.send_ws_message("msg", {"n": 1}, session_id="session-a")
    await _wait(stuck.entered)

    await manager.send_ws_message("msg", {"n": 2}, session_id="session-a")  # fills the queue (maxsize=1)
    await manager.send_ws_message("msg", {"n": 3}, session_id="session-a")  # overflow -> evict

    # The other session's delivery is unaffected by session-a's stalled socket.
    await manager.send_ws_message("msg", {"n": 1}, session_id="session-b")
    await _wait(fast.received_event)
    assert len(fast.received) == 1

    for _ in range(5):
        await asyncio.sleep(0)  # let the evict() task, scheduled via create_task, run
    assert "session-a" not in manager.session_connections
    assert stuck.close_codes == [WS_CLOSE_OVERFLOW]


@pytest.mark.asyncio
async def test_a_send_failure_evicts_only_that_connection():
    class _BrokenSocket(_FakeSocket):
        async def send_text(self, message: str) -> None:
            raise ConnectionResetError("boom")

    manager = ConnectionManager()
    broken = _BrokenSocket()
    ok = _FakeSocket()
    await manager.connect(broken, "session-a")
    await manager.connect(ok, "session-b")

    await manager.send_ws_message("msg", {"n": 1}, session_id="session-a")
    await manager.send_ws_message("msg", {"n": 1}, session_id="session-b")
    await _wait(ok.received_event)

    for _ in range(5):
        await asyncio.sleep(0)
    assert "session-a" not in manager.session_connections
    assert "session-b" in manager.session_connections


# ----------------------------------------------------------------------
# WS-05 — durable vs. ephemeral persistence
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_durable_event_is_persisted_before_send():
    manager = ConnectionManager()
    socket = _FakeSocket()
    await manager.connect(socket, "session-a")
    calls = []

    async def fake_save(log):
        from types import SimpleNamespace
        calls.append(log.event_type)
        return SimpleNamespace(event_id=7, payload=log.payload)

    import socket_store as ss
    orig = ss.BackendClient.save_event
    ss.BackendClient.save_event = fake_save
    try:
        await manager.send_ws_message("hypothesis", {"title": "x"}, session_id="session-a")
    finally:
        ss.BackendClient.save_event = orig
    await _wait(socket.received_event)

    assert calls == ["hypothesis"]
    assert socket.received[0]["event_id"] == 7


@pytest.mark.asyncio
async def test_an_ephemeral_event_is_never_persisted():
    manager = ConnectionManager()
    socket = _FakeSocket()
    await manager.connect(socket, "session-a")
    calls = []

    async def fake_save(log):
        calls.append(log.event_type)
        raise AssertionError("ephemeral events must not be persisted")

    import socket_store as ss
    orig = ss.BackendClient.save_event
    ss.BackendClient.save_event = fake_save
    try:
        await manager.send_ws_message("tool_update", {"tick": 1}, session_id="session-a")
    finally:
        ss.BackendClient.save_event = orig
    await _wait(socket.received_event)

    assert calls == []
    assert socket.received[0]["event_id"] is None


@pytest.mark.asyncio
async def test_an_unregistered_type_does_not_raise_and_is_persisted_as_durable():
    """classify() fails safe (event_registry.py): a registry gap must not
    crash an emitter that has no try/except around send_ws_message, such as
    the planner's output_handler (api/runners.py)."""
    manager = ConnectionManager()
    socket = _FakeSocket()
    await manager.connect(socket, "session-a")

    await manager.send_ws_message("some_future_type", {}, session_id="session-a")
    await _wait(socket.received_event)

    assert socket.received[0]["event_id"] == 1  # from the autouse stub_save_log fixture


# ----------------------------------------------------------------------
# WS-08 — handshake-cursor replay
# ----------------------------------------------------------------------


def _log(event_id: int, event_type: str = "hypothesis", payload=None) -> SessionEvent:
    return SessionEvent(session_id="session-a", event_type=event_type, payload=payload or {"n": event_id}, event_id=event_id)


async def _drain(socket: _FakeSocket, count: int, timeout: float = 1.0) -> list[dict]:
    """Wait until at least `count` frames have arrived, then return them."""
    async def _until_enough():
        while len(socket.received) < count:
            await asyncio.sleep(0)
    await asyncio.wait_for(_until_enough(), timeout=timeout)
    return socket.received


@pytest.mark.asyncio
async def test_replay_pages_the_full_history_in_order_then_sends_replay_complete(monkeypatch):
    """T4: a session with more events than one page (REPLAY_PAGE_SIZE=500)
    must not lose the newest ones -- the old single-call replay did (W3)."""
    total = socket_store.REPLAY_PAGE_SIZE + 200
    all_logs = [_log(i) for i in range(1, total + 1)]

    async def fake_replay_logs(session_id, after_event_id=0, limit=500):
        page = [log for log in all_logs if log.event_id > after_event_id][:limit]
        return page

    monkeypatch.setattr(socket_store.BackendClient, "replay_events", fake_replay_logs)

    manager = ConnectionManager()
    socket = _FakeSocket()
    connection = await manager.connect(socket, "session-a")

    last_event_id = await manager.replay(connection, after_event_id=0)

    frames = await _drain(socket, total + 1)  # + replay_complete
    assert last_event_id == total
    assert [f["event_id"] for f in frames[:total]] == list(range(1, total + 1))
    assert all(f["replaying"] is True for f in frames[:total])
    assert frames[total]["type"] == "replay_complete"
    assert frames[total]["payload"]["last_event_id"] == total


@pytest.mark.asyncio
async def test_reconnect_replay_delivers_exactly_the_events_since_the_cursor(monkeypatch):
    """T5: reconnecting with a stored cursor must replay only what was
    missed, not the whole history again."""
    missed = [_log(i) for i in range(91, 101)]  # 10 events, cursor was 90

    async def fake_replay_logs(session_id, after_event_id=0, limit=500):
        assert after_event_id == 90
        return missed if after_event_id == 90 else []

    monkeypatch.setattr(socket_store.BackendClient, "replay_events", fake_replay_logs)

    manager = ConnectionManager()
    socket = _FakeSocket()
    connection = await manager.connect(socket, "session-a")

    last_event_id = await manager.replay(connection, after_event_id=90)

    frames = await _drain(socket, 11)
    assert last_event_id == 100
    assert [f["event_id"] for f in frames[:10]] == list(range(91, 101))
    assert frames[10]["type"] == "replay_complete"


@pytest.mark.asyncio
async def test_an_event_committed_during_replay_is_delivered_exactly_once(monkeypatch):
    """T6: overlap dedup -- a live event that lands mid-replay must not
    arrive twice (once live, once again a page later from the log)."""
    manager = ConnectionManager()
    socket = _FakeSocket()
    connection = await manager.connect(socket, "session-a")
    call_count = 0
    page_size = socket_store.REPLAY_PAGE_SIZE
    first_page = [_log(i) for i in range(1, page_size + 1)]  # a full page, so
    # replay() keeps paging instead of stopping after this one (a page
    # shorter than the limit means "no more data" and ends the loop).

    async def fake_replay_logs(session_id, after_event_id=0, limit=500):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return first_page
        if call_count == 2:
            # Between that full first page and this fetch, a live event
            # commits and is delivered straight to the connection --
            # simulating it landing in the gap between two page fetches.
            await manager.send_ws_message(
                "hypothesis", {"n": page_size + 1},
                session_id="session-a", event_id=page_size + 1,
            )
            # This fetch now also finds it in the durable log, since it
            # committed for real before this second read.
            return [_log(page_size + 1)]
        return []

    monkeypatch.setattr(socket_store.BackendClient, "replay_events", fake_replay_logs)

    last_event_id = await manager.replay(connection, after_event_id=0)

    # The full first page, the live event exactly once, then replay_complete.
    frames = await _drain(socket, page_size + 2, timeout=5.0)
    assert last_event_id == page_size + 1
    event_ids = [f["event_id"] for f in frames if f["type"] != "replay_complete"]
    assert event_ids == list(range(1, page_size + 2))  # no repeat of page_size + 1


@pytest.mark.asyncio
async def test_msg_type_rows_are_excluded_from_replay(monkeypatch):
    """W7, per DQ6: chat text has its own durable home (chat_messages) and
    must not be replayed a second time from ws_logs."""
    async def fake_replay_logs(session_id, after_event_id=0, limit=500):
        if after_event_id == 0:
            return [_log(1, event_type="msg"), _log(2, event_type="hypothesis")]
        return []

    monkeypatch.setattr(socket_store.BackendClient, "replay_events", fake_replay_logs)

    manager = ConnectionManager()
    socket = _FakeSocket()
    connection = await manager.connect(socket, "session-a")

    await manager.replay(connection, after_event_id=0)

    frames = await _drain(socket, 2)  # event 2, then replay_complete -- never event 1
    assert [f["event_id"] for f in frames] == [2, None]
    assert frames[1]["type"] == "replay_complete"


@pytest.mark.asyncio
async def test_a_replay_failure_sends_an_error_frame_and_returns_none_not_an_empty_success(monkeypatch):
    """W4: a failed replay must never look like a successful, empty one."""
    async def failing_replay_logs(session_id, after_event_id=0, limit=500):
        raise RuntimeError("application_api unreachable")

    monkeypatch.setattr(socket_store.BackendClient, "replay_events", failing_replay_logs)

    manager = ConnectionManager()
    socket = _FakeSocket()
    connection = await manager.connect(socket, "session-a")

    result = await manager.replay(connection, after_event_id=0)

    frames = await _drain(socket, 1)
    assert result is None
    assert frames[0]["type"] == "replay_failed"


@pytest.mark.asyncio
async def test_replay_never_reaches_a_sibling_socket_on_the_same_session(monkeypatch):
    """T7: opening one socket's replay must send nothing to another socket
    on the same session (fixes W2's broadcast-replay)."""
    async def fake_replay_logs(session_id, after_event_id=0, limit=500):
        return [_log(1)] if after_event_id == 0 else []

    monkeypatch.setattr(socket_store.BackendClient, "replay_events", fake_replay_logs)

    manager = ConnectionManager()
    socket_a, socket_b = _FakeSocket(), _FakeSocket()
    connection_a = await manager.connect(socket_a, "session-a")
    await manager.connect(socket_b, "session-a")

    await manager.replay(connection_a, after_event_id=0)
    await _drain(socket_a, 2)

    assert socket_b.received == []


# ----------------------------------------------------------------------
# WS-10 -- POST /session/{id}/input's Idempotency-Key dedup
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_to_queue_queues_when_no_idempotency_key_is_given():
    manager = ConnectionManager()

    queued = await manager.push_to_queue("hello", session_id="session-a")

    assert queued is True
    assert manager.message_queues["session-a"].get_nowait() == "hello"


@pytest.mark.asyncio
async def test_push_to_queue_skips_a_repeated_idempotency_key():
    manager = ConnectionManager()

    first = await manager.push_to_queue(
        "hello", session_id="session-a", idempotency_key="retry-1"
    )
    second = await manager.push_to_queue(
        "hello", session_id="session-a", idempotency_key="retry-1"
    )

    assert first is True
    assert second is False
    assert manager.message_queues["session-a"].qsize() == 1


@pytest.mark.asyncio
async def test_push_to_queue_idempotency_is_scoped_per_session():
    manager = ConnectionManager()

    await manager.push_to_queue("a", session_id="session-a", idempotency_key="shared-key")
    await manager.push_to_queue("b", session_id="session-b", idempotency_key="shared-key")

    assert manager.message_queues["session-a"].qsize() == 1
    assert manager.message_queues["session-b"].qsize() == 1


@pytest.mark.asyncio
async def test_push_to_queue_idempotency_keys_are_bounded_per_session():
    """Older keys are forgotten past the per-session cap, rather than growing
    a session's dedup memory forever -- see INPUT_IDEMPOTENCY_KEYS_PER_SESSION."""
    manager = ConnectionManager()
    cap = socket_store.INPUT_IDEMPOTENCY_KEYS_PER_SESSION

    for i in range(cap + 1):
        await manager.push_to_queue(str(i), session_id="session-a", idempotency_key=f"key-{i}")

    # The very first key has aged out, so it is treated as new again.
    replay_of_first = await manager.push_to_queue(
        "0-again", session_id="session-a", idempotency_key="key-0"
    )
    assert replay_of_first is True

    # A recent key is still remembered.
    replay_of_last = await manager.push_to_queue(
        "last-again", session_id="session-a", idempotency_key=f"key-{cap}"
    )
    assert replay_of_last is False


@pytest.mark.asyncio
async def test_clear_message_queue_forgets_that_session_s_idempotency_keys():
    manager = ConnectionManager()
    await manager.push_to_queue("hello", session_id="session-a", idempotency_key="key-1")

    manager.clear_message_queue("session-a")

    # A key from before the reset is not a "duplicate" of anything post-reset.
    queued_again = await manager.push_to_queue(
        "hello again", session_id="session-a", idempotency_key="key-1"
    )
    assert queued_again is True
