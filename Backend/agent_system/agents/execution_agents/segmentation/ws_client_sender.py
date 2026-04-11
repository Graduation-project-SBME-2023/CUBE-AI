import asyncio
import json
import logging
import os
import aiohttp  # pip install aiohttp
import websockets
logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("MAS_BASE_URL", "http://localhost:8150")

async def send_ws_message_remote(msg_type: str, payload: dict, session_id=None):
    async with aiohttp.ClientSession() as session:
        await session.post(
            f"{BASE_URL}/ws-broadcast",
            json={"type": msg_type, "payload": payload, "session_id": session_id}
        )
        logger.info(f"[ws_client] sent '{msg_type}' via HTTP broadcast")

async def wait_for_user_message_remote(session_id=None, timeout=600.0):
    ws_url = os.environ.get("MAS_WS_URL", "ws://localhost:8150/ws")
    logger.info(f"[ws_client] waiting for user reply...")
    async with websockets.connect(ws_url) as ws:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        logger.info(f"[ws_client] received user reply")
        return raw