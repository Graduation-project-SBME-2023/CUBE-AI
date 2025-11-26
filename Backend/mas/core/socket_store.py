# socket_store.py

from fastapi import WebSocket
import json
import asyncio

_active_socket: WebSocket | None = None


def set_socket(ws: WebSocket | None):
    global _active_socket
    _active_socket = ws


def get_socket() -> WebSocket | None:
    return _active_socket


async def send_ws_message(msg_type: str, payload):
    ws = get_socket()
    if ws is None:
        return

    try:
        await ws.send_text(json.dumps({
            "type": msg_type,
            "payload": payload,
        }, default=str))
    except Exception:
        set_socket(None)


async def wait_for_user_message(expected_type: str | None = None):
    ws = get_socket()
    if ws is None:
        raise RuntimeError("No active WebSocket connection")

    while True:
        try:
            raw = await ws.receive_text()
            msg = json.loads(raw)   

            return msg

        except Exception:
            # connection closed by client
            set_socket(None)
            return None

async def close_ws():
    ws = get_socket()
    if ws is not None:
        await ws.close()
        set_socket(None)