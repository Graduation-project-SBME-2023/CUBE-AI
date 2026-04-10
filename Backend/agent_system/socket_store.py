import json
import asyncio
import logging
from typing import List, Any, Dict
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        # Holds the active socket connection(s)
        self.active_connections: List[WebSocket] = []
        self.solely_connected_socket: WebSocket | None = None

        # Per-session queues buffer user input for each planner session.
        self.message_queues: Dict[str, asyncio.Queue] = {}

    def _get_queue(self, session_id: str | None) -> asyncio.Queue:
        key = session_id or "default"
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

    async def send_ws_message(self, msg_type: str, payload: Any, session_id: str | None = None):
        """
        Called by the Planner/Agent to send updates to the UI.
        """
        message_data = json.dumps({
            "type": msg_type,
            "payload": payload,
            "session_id": session_id,
        }, default=str)

        dead_sockets = []
        for connection in self.active_connections:
            try:
                print(f"will send to frontend msg: {message_data}")
                await connection.send_text(message_data)
            except Exception as e:
                logger.error(f"Failed to send message: {e}")
                dead_sockets.append(connection)

        # if self.solely_connected_socket:
        #     await self.solely_connected_socket.send_text(message_data)

        # Cleanup broken connections
        for dead in dead_sockets:
            self.disconnect(dead)


# Create a global singleton instance to be imported by main.py and planner.py
manager = ConnectionManager()


# --- Helper functions to maintain your existing import signature ---

async def send_ws_message(msg_type: str, payload: Any, session_id: str | None = None):
    await manager.send_ws_message(msg_type, payload, session_id=session_id)


async def wait_for_user_message(ws: WebSocket = None, session_id: str | None = None):
    # Note: We ignore the 'ws' argument here because the manager handles it globally.
    return await manager.wait_for_user_message(session_id=session_id)


def get_socket():
    # Helper if you need access to connections, though rarely needed now
    return manager.active_connections
