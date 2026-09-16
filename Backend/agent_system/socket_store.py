import copy
import json
import asyncio
import logging
from typing import List, Any, Dict
from fastapi import WebSocket
from clients.backend_client import BackendClient
from models.logs import WS_Log
from services.timeout_policy import WS_SEND_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        # Holds the active socket connection(s)
        self.active_connections: List[WebSocket] = []
        self.solely_connected_socket: WebSocket | None = None

        # Per-session queues buffer user input for each planner session.
        self.message_queues: Dict[str, asyncio.Queue] = {}

    def _get_queue(self, session_id: str | None) -> asyncio.Queue:
        # Callers pass the session id as either a `str` (from a parsed WS
        # JSON payload) or a `UUID` (routes type PlanningRequest.session_id
        # as UUID, and it flows unconverted into the in-memory Session
        # dataclass, which — unlike Pydantic — never coerces it). Those hash
        # differently, so without normalizing here, the planner's queue.get()
        # and the WS handler's queue.put() land on two different queues and
        # the wait never wakes up.
        key = str(session_id) if session_id else "default"
        if key not in self.message_queues:
            self.message_queues[key] = asyncio.Queue()
        return self.message_queues[key]

    async def connect(self, websocket: WebSocket):
        """Called when a new client connects via WebSocket."""
        await websocket.accept()
        self.active_connections.append(websocket)
        if self.solely_connected_socket is None:
            self.solely_connected_socket = websocket
        logger.info("WebSocket client connected.")

    def disconnect(self, websocket: WebSocket):
        """Called when a client disconnects."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info("WebSocket client disconnected.")
        if self.solely_connected_socket is websocket:
            self.solely_connected_socket = (
                self.active_connections[0] if self.active_connections else None
            )

    def clear_message_queue(self, session_id: str | None) -> int:
        """Discard input only when the owning session lifecycle is reset."""
        key = str(session_id) if session_id else "default"
        queue = self.message_queues.pop(key, None)
        discarded = queue.qsize() if queue is not None else 0
        if discarded:
            logger.info(
                "Discarded %s queued messages for reset session=%s",
                discarded,
                key,
            )
        return discarded

    async def push_to_queue(self, raw_text: str, session_id: str | None = None):
        """
        Called by the FastAPI route listener when client sends a message.
        We put it in the queue for the Planner to pick up.
        """
        try:
            queue = self._get_queue(session_id)
            await queue.put(raw_text)
            logger.info(f"Message queued for session={session_id}: {raw_text[:100]}")
        except Exception as e:
            logger.error(f"Error processing incoming message: {e}")

    async def wait_for_user_message(self, session_id: str | None = None) -> str:
        """
        Called by the Planner/Agent.
        This blocks execution here until the Queue has an item.
        """
        # This will pause the Agent until the FastAPI route receives data
        queue = self._get_queue(session_id)
        message = await queue.get()
        return message

    async def send_ws_message(
        self,
        msg_type: str,
        payload: Any,
        session_id: str = '',
        replaying: bool = False,
        event_id: int | None = None,
    ):
        """Persist the event, then broadcast it carrying its `event_id`."""
        # Snapshot before anything awaits: streaming tool output keeps
        # appending to the same dict, and an event whose payload changed
        # between being emitted and being persisted is not a record of what
        # happened. (This is the hazard the legacy background-save path had;
        # persisting first does not remove it, it moves it.)
        payload = copy.deepcopy(payload)
        # Callers routinely pass the session's UUID object rather than str;
        # WS_Log and the outgoing envelope both require a plain string.
        session_id = str(session_id) if session_id else session_id

        if not replaying and event_id is None:
            try:
                saved = await BackendClient.save_log(
                    WS_Log(msg_type=msg_type, payload=payload, session_id=session_id)
                )
                event_id = saved.event_id
            except Exception as exc:
                logger.error(
                    "Event %s for session %s was not persisted and will not be "
                    "replayable: %s",
                    msg_type,
                    session_id,
                    exc,
                )

        message_data = json.dumps({
            "type": msg_type,
            "payload": payload,
            "session_id": session_id,
            "replaying": replaying,
            "event_id": event_id,
        }, default=str)

        dead_sockets = []
        for connection in self.active_connections:
            try:
                await asyncio.wait_for(
                    connection.send_text(message_data),
                    timeout=WS_SEND_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "WebSocket send timed out after %.1fs",
                    WS_SEND_TIMEOUT_SECONDS,
                )
                dead_sockets.append(connection)
            except Exception as e:
                logger.error(f"Failed to send message: {e}")
                dead_sockets.append(connection)

        # Cleanup broken connections
        for dead in dead_sockets:
            self.disconnect(dead)


# Create a global singleton instance to be imported by main.py and planner.py
manager = ConnectionManager()


# --- Helper functions to maintain your existing import signature ---

async def send_ws_message(
    msg_type: str,
    payload: Any,
    session_id: str | None = None,
    event_id: int | None = None,
):
    await manager.send_ws_message(
        msg_type, payload, session_id=session_id, event_id=event_id
    )


async def wait_for_user_message(ws: WebSocket = None, session_id: str | None = None):
    # Note: We ignore the 'ws' argument here because the manager handles it globally.
    return await manager.wait_for_user_message(session_id=session_id)


def get_socket():
    # Helper if you need access to connections, though rarely needed now
    return manager.active_connections
