# socket_store.py

from fastapi import WebSocket
import json
import asyncio

# Thread-safe set of connected WebSocket clients
_active_sockets: set[WebSocket] = set()
_lock = asyncio.Lock()


async def set_socket(ws: WebSocket):
    async with _lock:
        _active_sockets.add(ws)

async def remove_socket(ws: WebSocket):
    async with _lock:
        if ws in _active_sockets:
            _active_sockets.remove(ws)


async def get_socket() -> list[WebSocket]:
    async with _lock:
        return list(_active_sockets)




async def send_ws_message(msg_type: str, payload):
    message = json.dumps({
        "type": msg_type,
        "payload": payload,
    }, default=str)

    async with _lock:
        dead_sockets = []

        for ws in _active_sockets:
            try:
                await ws.send_text(message)
            except Exception:
                dead_sockets.append(ws)
        
        # Remove disconnected sockets
        for ws in dead_sockets:
            _active_sockets.remove(ws)


async def wait_for_user_message(ws: WebSocket):
    try:
        data = await ws.receive_text()
        return json.loads(data)
    except Exception:
        await remove_socket(ws)
        return None
    

async def close_ws(ws: WebSocket):
    try:
        await ws.close()
    finally:
        await remove_socket(ws)