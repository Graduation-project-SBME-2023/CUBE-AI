"""Client WebSocket endpoint."""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from socket_store import manager as ws_manager

router = APIRouter()


# ----------------------------------------------------------------------
# WebSocket /ws
# ----------------------------------------------------------------------


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    logging.getLogger("HypothesisFlow").info("[HypothesisFlow][WS] Client connected")

    try:
        while True:
            data = await websocket.receive_text()
            # NOTE: Do NOT push_to_queue here — that caused every message to be
            # queued twice (once as raw text, once after JSON parsing).
            # Only queue inside the typed message block below.
            logging.getLogger("HypothesisFlow").info(
                "[HypothesisFlow][WS] Message received raw | size=%s",
                len(data or ""),
            )

            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                # Backward compatibility: plain text is treated as user input.
                # Queued exactly ONCE here.
                await ws_manager.push_to_queue(data)
                continue

            if not isinstance(message, dict):
                continue

            msg_type = message.get("type")
            payload = message.get("payload")
            session_id = message.get("session_id")

            # Some clients may nest session_id inside payload.
            if not session_id and isinstance(payload, dict):
                session_id = payload.get("session_id")

            if msg_type in {"user", "user_input", "chat_input"}:
                if isinstance(payload, dict):
                    text = payload.get("text") or payload.get("message") or ""
                else:
                    text = str(payload or "")

                if text.strip():
                    logging.getLogger("HypothesisFlow").info(
                        "[HypothesisFlow][WS] Queueing user text | session_id=%s | text_len=%s | preview=%s",
                        session_id,
                        len(text),
                        text[:120],
                    )
                    await ws_manager.push_to_queue(text, session_id=session_id)

    except WebSocketDisconnect:
        logging.getLogger("HypothesisFlow").info(
            "[HypothesisFlow][WS] Client disconnected"
        )
        ws_manager.disconnect(websocket)
