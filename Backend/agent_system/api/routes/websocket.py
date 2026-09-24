"""Client WebSocket endpoint."""

import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from socket_store import manager as ws_manager, WS_CLOSE_REPLAY_FAILED
from api.routes.sessions import send_segmentation_catchup_snapshot

router = APIRouter()

logger = logging.getLogger("HypothesisFlow")


def _is_valid_session_id(value: str | None) -> bool:
    if not value:
        return False
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _parse_after_event_id(value: str | None) -> int:
    """Defensively parsed, unlike session_id: a missing or malformed cursor
    just means "replay everything" (0), not a handshake rejection -- a stale
    bundle sending a garbage cursor should still be able to connect and catch
    up, not be locked out (WEBSOCKET_FIX_PLAN.md WS-08)."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed >= 0 else 0


# ----------------------------------------------------------------------
# WebSocket /ws
# ----------------------------------------------------------------------


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    session_id = websocket.query_params.get("session_id")
    if not _is_valid_session_id(session_id):
        # Close before accept() so a stale or misconfigured client fails
        # loudly (1008 = policy violation) instead of connecting and
        # silently receiving nothing. See WEBSOCKET_FIX_PLAN.md W1/WS-02:
        # a socket with no bound session is how one session's events used to
        # reach another session's tab.
        logger.warning(
            "[HypothesisFlow][WS] Rejected connection with invalid session_id=%r",
            session_id,
        )
        await websocket.close(code=1008)
        return
    after_event_id = _parse_after_event_id(websocket.query_params.get("after_event_id"))

    connection = await ws_manager.connect(websocket, session_id)
    logger.info(
        "[HypothesisFlow][WS] Client connected | session_id=%s | after_event_id=%s",
        session_id, after_event_id,
    )

    # Handshake-cursor catch-up (WEBSOCKET_FIX_PLAN.md WS-08, principle 5):
    # registering the connection above before reading anything here is what
    # guarantees no gap -- an event committed during this replay is either in
    # the page replay() reads or already queued on this connection, and its
    # own overlap dedup makes "both" harmless.
    last_event_id = await ws_manager.replay(connection, after_event_id)
    if last_event_id is None:
        # The durable log could not be read -- an error frame was already
        # sent (see replay()); close so the client retries rather than
        # sitting in a silently incomplete session (fixes W4).
        ws_manager.disconnect(websocket)
        await websocket.close(code=WS_CLOSE_REPLAY_FAILED)
        return
    await send_segmentation_catchup_snapshot(ws_manager, connection, session_id)

    try:
        while True:
            # The socket is push-only now (WEBSOCKET_FIX_PLAN.md WS-10,
            # principle 1): user text, chat input and plan edits all arrive
            # over REST (POST /session/{id}/input and friends), not here.
            # This loop's only remaining job is to keep pumping the ASGI
            # receive() so a client-initiated close still surfaces as
            # WebSocketDisconnect below -- without it, a dead connection
            # would stay registered until some later send failed on it (W6).
            # Anything a stale client still sends is logged and dropped, not
            # acted on.
            data = await websocket.receive_text()
            logger.warning(
                "[HypothesisFlow][WS] Ignoring inbound frame; the socket is "
                "push-only (WS-10) | session_id=%s | size=%s",
                session_id,
                len(data or ""),
            )

    except WebSocketDisconnect:
        logger.info(
            "[HypothesisFlow][WS] Client disconnected | session_id=%s", session_id
        )
    finally:
        # Unconditional, unlike the old bare `except WebSocketDisconnect`:
        # any other exception from receive_text() used to skip this and
        # leave a dead socket in the routing table until a later send failed
        # on it (W6).
        ws_manager.disconnect(websocket)
