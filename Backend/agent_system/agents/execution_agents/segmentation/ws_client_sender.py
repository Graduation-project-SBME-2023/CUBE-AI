import logging
import os
import httpx

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("MAS_BASE_URL", "http://localhost:8150")


async def send_ws_message_remote(
    msg_type: str,
    payload: dict,
    session_id=None,
    *,
    client: httpx.AsyncClient | None = None,
    base_url: str | None = None,
    timeout: float = 60.0,
):
    request_client = client or httpx.AsyncClient()
    token = os.environ.get("WS_INTERNAL_TOKEN", "")
    headers = {"X-Internal-Token": token} if token else {}
    try:
        response = await request_client.post(
            f"{(base_url or BASE_URL).rstrip('/')}/ws-broadcast",
            json={
                "type": msg_type,
                "payload": payload,
                "session_id": session_id,
            },
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
    finally:
        if client is None:
            await request_client.aclose()
    logger.info("[ws_client] sent %r via HTTP broadcast", msg_type)
