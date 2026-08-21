import asyncio
import copy
import json
import logging
import os
import re
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4

from fastapi import WebSocket

from clients.backend_client import BackendClient
from models.logs import WS_Log
from services.best_effort import schedule_best_effort
from services.timeout_policy import WS_SEND_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_DURABLE_REPLAY_EVENTS = frozenset(
    {
        "lit_review_output",
        "population_output",
        "segmentation_output",
        "features_output",
        "modeling_output",
        "report_output",
        "latex_output",
    }
)


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def normalize_session_id(session_id: str | None) -> str:
    value = str(session_id or "").strip()
    if not value:
        raise ValueError("session_id is required")
    if len(value) > 128:
        raise ValueError("session_id is too long")
    if not _SESSION_ID_RE.fullmatch(value):
        raise ValueError("session_id contains unsupported characters")
    return value


class WebSocketCapacityExceeded(RuntimeError):
    """Raised when the process cannot safely accept another socket."""


class ConnectionManager:
    def __init__(self):
        self.connections_by_session: Dict[str, set[WebSocket]] = {}
        self.session_by_connection: Dict[WebSocket, str] = {}
        self._outbound_queues: Dict[WebSocket, asyncio.Queue[str]] = {}
        self._sender_tasks: Dict[WebSocket, asyncio.Task] = {}
        self._closing_connections: set[WebSocket] = set()
        self._connection_lock = asyncio.Lock()
        self._session_sequences: Dict[str, int] = {}
        self._outbound_queue_size = _positive_int("WS_OUTBOUND_QUEUE_SIZE", 256)
        self._input_queue_size = _positive_int("WS_INPUT_QUEUE_SIZE", 32)
        self._session_idle_ttl_seconds = _positive_int(
            "WS_SESSION_IDLE_TTL_SECONDS", 3600
        )
        self._client_message_history_size = _positive_int(
            "WS_CLIENT_MESSAGE_HISTORY", 1024
        )
        self._max_connections = _positive_int("WS_MAX_CONNECTIONS", 256)
        self._max_connections_per_session = _positive_int(
            "WS_MAX_CONNECTIONS_PER_SESSION", 8
        )
        self._session_last_activity: Dict[str, float] = {}
        self._queue_waiters: Dict[str, int] = {}
        self._client_message_ids: Dict[str, set[str]] = {}
        self._client_message_order: Dict[str, deque[str]] = {}

        # Planner input is isolated from socket lifetime. A reconnect must not
        # discard input that the owning session has already submitted.
        self.message_queues: Dict[str, asyncio.Queue[str]] = {}

    @property
    def active_connections(self) -> list[WebSocket]:
        """Compatibility view for diagnostics; routing must use the session map."""
        return list(self.session_by_connection)

    def _get_queue(self, session_id: str | None) -> asyncio.Queue[str]:
        key = normalize_session_id(session_id)
        self._touch_session(key)
        if key not in self.message_queues:
            self.message_queues[key] = asyncio.Queue(maxsize=self._input_queue_size)
        return self.message_queues[key]

    async def connect(self, websocket: WebSocket, session_id: str) -> None:
        key = normalize_session_id(session_id)
        async with self._connection_lock:
            self._prune_inactive_sessions()
            if self.connection_count() >= self._max_connections:
                raise WebSocketCapacityExceeded("WebSocket capacity is full")
            if self.connection_count(key) >= self._max_connections_per_session:
                raise WebSocketCapacityExceeded(
                    "Too many connections for this session"
                )

            self._touch_session(key)
            await websocket.accept()
            self.connections_by_session.setdefault(key, set()).add(websocket)
            self.session_by_connection[websocket] = key
            outbound_queue: asyncio.Queue[str] = asyncio.Queue(
                maxsize=self._outbound_queue_size
            )
            self._outbound_queues[websocket] = outbound_queue
            self._sender_tasks[websocket] = asyncio.create_task(
                self._sender_loop(websocket, key, outbound_queue),
                name=f"ws-sender:{key}",
            )
        logger.info(
            "WebSocket connected session=%s session_connections=%s total_connections=%s",
            key,
            len(self.connections_by_session[key]),
            len(self.session_by_connection),
        )

    def disconnect(self, websocket: WebSocket) -> None:
        session_id = self.session_by_connection.pop(websocket, None)
        if session_id is not None:
            self._touch_session(session_id)
            session_connections = self.connections_by_session.get(session_id)
            if session_connections is not None:
                session_connections.discard(websocket)
                if not session_connections:
                    self.connections_by_session.pop(session_id, None)

        self._outbound_queues.pop(websocket, None)
        self._closing_connections.discard(websocket)
        sender_task = self._sender_tasks.pop(websocket, None)
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        if sender_task is not None and sender_task is not current_task:
            sender_task.cancel()

        if session_id is not None:
            logger.info(
                "WebSocket disconnected session=%s total_connections=%s",
                session_id,
                len(self.session_by_connection),
            )

    async def _sender_loop(
        self,
        websocket: WebSocket,
        session_id: str,
        queue: asyncio.Queue[str],
    ) -> None:
        try:
            while True:
                message = await queue.get()
                try:
                    await asyncio.wait_for(
                        websocket.send_text(message),
                        timeout=WS_SEND_TIMEOUT_SECONDS,
                    )
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.warning(
                "WebSocket send timed out session=%s timeout_seconds=%.1f",
                session_id,
                WS_SEND_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.warning(
                "WebSocket sender failed session=%s",
                session_id,
                exc_info=True,
            )
        finally:
            self.disconnect(websocket)

    async def shutdown(self) -> None:
        sockets = tuple(self.session_by_connection)
        tasks = tuple(self._sender_tasks.values())
        for websocket in sockets:
            try:
                await websocket.close(code=1001, reason="Server shutting down")
            except Exception:
                pass
            self.disconnect(websocket)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _close_slow_connection(self, websocket: WebSocket) -> None:
        session_id = self.session_by_connection.get(websocket)
        try:
            await websocket.close(code=1013, reason="Client is not consuming events")
        except Exception:
            pass
        finally:
            logger.warning("Disconnected slow WebSocket session=%s", session_id)
            self.disconnect(websocket)

    def _schedule_slow_connection_close(self, websocket: WebSocket) -> None:
        if websocket in self._closing_connections:
            return
        self._closing_connections.add(websocket)
        asyncio.create_task(self._close_slow_connection(websocket))

    def clear_message_queue(self, session_id: str | None) -> int:
        key = normalize_session_id(session_id)
        queue = self.message_queues.pop(key, None)
        discarded = queue.qsize() if queue is not None else 0
        if discarded:
            logger.info(
                "Discarded %s queued messages for reset session=%s",
                discarded,
                key,
            )
        return discarded

    async def push_to_queue(self, raw_text: str, session_id: str | None = None) -> bool:
        key = normalize_session_id(session_id)
        self._prune_inactive_sessions()
        queue = self._get_queue(key)
        try:
            queue.put_nowait(raw_text)
        except asyncio.QueueFull:
            logger.warning("Planner input queue is full session=%s", key)
            return False

        # Do not log clinical/user text. Session and size are sufficient for tracing.
        logger.info("Message queued session=%s size=%s", key, len(raw_text))
        return True

    async def wait_for_user_message(self, session_id: str | None = None) -> str:
        key = normalize_session_id(session_id)
        queue = self._get_queue(key)
        self._queue_waiters[key] = self._queue_waiters.get(key, 0) + 1
        try:
            return await queue.get()
        finally:
            remaining = self._queue_waiters.get(key, 1) - 1
            if remaining > 0:
                self._queue_waiters[key] = remaining
            else:
                self._queue_waiters.pop(key, None)
            self._touch_session(key)

    def _touch_session(self, session_id: str) -> None:
        self._session_last_activity[session_id] = time.monotonic()

    def remember_client_message(self, session_id: str, message_id: str) -> bool:
        key = normalize_session_id(session_id)
        normalized_id = str(message_id or "").strip()
        if not normalized_id or len(normalized_id) > 128:
            raise ValueError("message_id must contain between 1 and 128 characters")

        seen = self._client_message_ids.setdefault(key, set())
        if normalized_id in seen:
            return False

        order = self._client_message_order.setdefault(key, deque())
        seen.add(normalized_id)
        order.append(normalized_id)
        while len(order) > self._client_message_history_size:
            seen.discard(order.popleft())
        self._touch_session(key)
        return True

    def forget_client_message(self, session_id: str, message_id: str) -> None:
        key = normalize_session_id(session_id)
        normalized_id = str(message_id or "").strip()
        seen = self._client_message_ids.get(key)
        order = self._client_message_order.get(key)
        if seen is None or normalized_id not in seen:
            return
        seen.discard(normalized_id)
        if order is not None:
            try:
                order.remove(normalized_id)
            except ValueError:
                pass

    def _prune_inactive_sessions(self) -> None:
        cutoff = time.monotonic() - self._session_idle_ttl_seconds
        for session_id, last_activity in tuple(self._session_last_activity.items()):
            queue = self.message_queues.get(session_id)
            if last_activity > cutoff:
                continue
            if self.connections_by_session.get(session_id):
                continue
            if self._queue_waiters.get(session_id, 0):
                continue
            if queue is not None and not queue.empty():
                continue
            self.message_queues.pop(session_id, None)
            self._session_sequences.pop(session_id, None)
            self._client_message_ids.pop(session_id, None)
            self._client_message_order.pop(session_id, None)
            self._session_last_activity.pop(session_id, None)

    def _next_sequence(self, session_id: str) -> int:
        sequence = self._session_sequences.get(session_id, 0) + 1
        self._session_sequences[session_id] = sequence
        return sequence

    def build_event(
        self,
        msg_type: str,
        payload: Any,
        session_id: str,
        *,
        replaying: bool = False,
        event_id: str | None = None,
        sequence: int | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        key = normalize_session_id(session_id)
        self._touch_session(key)
        return {
            "version": 1,
            "event_id": event_id or str(uuid4()),
            "sequence": sequence if sequence is not None else self._next_sequence(key),
            "session_id": key,
            "run_id": payload.get("run_id") if isinstance(payload, dict) else None,
            "type": msg_type,
            "payload": payload,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            "replaying": replaying,
        }

    def send_to_connection(
        self,
        websocket: WebSocket,
        msg_type: str,
        payload: Any,
    ) -> bool:
        session_id = self.session_by_connection.get(websocket)
        queue = self._outbound_queues.get(websocket)
        if session_id is None or queue is None:
            return False
        event = self.build_event(msg_type, payload, session_id)
        try:
            queue.put_nowait(json.dumps(event, default=str))
            return True
        except asyncio.QueueFull:
            self._schedule_slow_connection_close(websocket)
            return False

    async def send_ws_message(
        self,
        msg_type: str,
        payload: Any,
        session_id: str,
        replaying: bool = False,
    ) -> dict[str, Any]:
        event = self.build_event(
            msg_type,
            payload,
            session_id,
            replaying=replaying,
        )
        message_data = json.dumps(event, default=str)
        session_connections = tuple(
            self.connections_by_session.get(event["session_id"], ())
        )

        for connection in session_connections:
            queue = self._outbound_queues.get(connection)
            if queue is None:
                continue
            try:
                queue.put_nowait(message_data)
            except asyncio.QueueFull:
                self._schedule_slow_connection_close(connection)

        if not replaying:
            log = WS_Log(
                uuid=event["event_id"],
                msg_type=msg_type,
                payload=payload,
                session_id=event["session_id"],
            )
            log_snapshot = copy.deepcopy(log)
            if msg_type in _DURABLE_REPLAY_EVENTS:
                saved_log = await BackendClient.save_log(log_snapshot)
                if saved_log is None:
                    logger.error(
                        "Durable replay event was not persisted "
                        "[session=%s event=%s type=%s]",
                        event["session_id"],
                        event["event_id"],
                        msg_type,
                    )
            else:
                schedule_best_effort(
                    lambda _log=log_snapshot: BackendClient.save_log(_log),
                    name=f"save-ws-log:{msg_type}",
                )

        return event

    def connection_count(self, session_id: str | None = None) -> int:
        if session_id is None:
            return len(self.session_by_connection)
        key = normalize_session_id(session_id)
        return len(self.connections_by_session.get(key, ()))


manager = ConnectionManager()


async def send_ws_message(
    msg_type: str,
    payload: Any,
    session_id: str | None = None,
):
    return await manager.send_ws_message(
        msg_type,
        payload,
        session_id=normalize_session_id(session_id),
    )


async def wait_for_user_message(
    ws: WebSocket = None,
    session_id: str | None = None,
):
    return await manager.wait_for_user_message(session_id=session_id)


def get_socket():
    return manager.connections_by_session
