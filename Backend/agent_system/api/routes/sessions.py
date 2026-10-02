"""Session replay, recovery, and history endpoints."""

import logging
import os

import httpx
from fastapi import APIRouter, Header, HTTPException

from storage_paths import workspace
from socket_store import manager as ws_manager
from models.logs import SessionEvent
from models.requests import SessionInputRequest
from services import population_review
from clients.backend_client import BackendClient
from agents.execution_agents.segmentation.progressive import build_snapshot_from_manifest
from api.runtime import session_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ----------------------------------------------------------------------
# State Recovery
# ----------------------------------------------------------------------


async def send_segmentation_catchup_snapshot(
    ws_manager, connection, session_id: str
) -> None:
    """Reconstruct the current segmentation-review state (from
    segmentation_batches/pairs, including the doctor's draft decisions) and
    deliver it to exactly the connection that just completed its handshake
    replay (WEBSOCKET_FIX_PLAN.md WS-08, principle 5 step 2, "the trailing
    segmentation snapshot is sent on this socket only").

    This is not the same thing as replaying `segmentation_output` rows from
    session_events: the review may have moved on since the last such event was
    emitted (further doctor decisions, pair resolutions), so this always
    rebuilds current truth from the authoritative tables rather than trusting
    the log to still describe it.
    """
    workspace_dir = str(workspace(session_id))
    try:
        batch = await population_review.current_batch(session_id)
        if not batch or batch["review_status"] == "not_ready":
            return
        manifest = population_review.snapshot_manifest(batch)
        snapshot = build_snapshot_from_manifest(
            manifest,
            workspace_dir,
            os.environ.get("MAS_BASE_URL", "http://localhost:8150"),
        )
        if snapshot:
            await ws_manager.send_replaying_frame(
                connection, "segmentation_output", snapshot
            )
    except (population_review.DomainError, httpx.HTTPError, ValueError, KeyError):
        logger.warning(
            "Could not reconstruct segmentation snapshot for session %s",
            session_id,
            exc_info=True,
        )


@router.get("/session/{session_id}/replay-logs")
async def replay_events(session_id: str, after_event_id: int = 0, limit: int = 2000):
    """Read-only: a session's events from a cursor, as JSON.

    This used to also broadcast every returned row over every socket in the
    process (WEBSOCKET_FIX_PLAN.md W2) -- that is now the WebSocket
    handshake's own job (`/ws?session_id=...&after_event_id=...`,
    `socket_store.ConnectionManager.replay`, WS-08), scoped to the one
    connecting socket. Nothing here sends anything over any socket; this
    route exists only for tooling/debugging that wants the raw log.
    """
    logs: list[SessionEvent] = await BackendClient.replay_events(
        session_id, after_event_id=after_event_id, limit=limit
    )
    return logs



@router.get("/session/{session_id}/config")
async def get_session_config(session_id: str):
    """The session's population configuration — read from its cohort."""
    try:
        cohort = await population_review.get_cohort(session_id)
    except population_review.DomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return population_review.cohort_state(cohort)

@router.get("/session/{session_id}/plan-state")
async def get_plan_state(session_id: str):
    """
    Get the session's hypothesis for recovery from history.

    PostgreSQL is the source of truth (sessions.intent); there is no
    workspace file to read. `task_messages` was never populated by any
    writer and is kept in the response only for API-shape compatibility.
    """
    hypothesis = await BackendClient.get_intent(session_id)
    if hypothesis is None:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id": session_id,
        "hypothesis": hypothesis.model_dump(),
        "task_messages": [],
    }


@router.get("/session/{session_id}/execution-state")
async def get_execution_state(session_id: str):
    state = await session_service.recover_execution_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="No execution state found")
    return state


@router.get("/session/{session_id}/chat-messages")
async def get_session_chat_messages(session_id: str):
    """
    Fetch chat messages for a session from the MONAI server.
    Used when recovering a session from hypothesis history.
    """
    async with httpx.AsyncClient() as client:
        monai_url = os.environ.get(
            "APP_API_URL",
            os.environ.get("CONTROL_API_URL", "http://localhost:8001"),
        )
        try:
            response = await client.get(
                f"{monai_url}/chat-messages/{session_id}", timeout=10.0
            )
            if response.status_code == 200:
                return response.json()
            else:
                return []
        except Exception as e:
            logger.warning(f"Could not fetch chat messages: {e}")
            return []


# ----------------------------------------------------------------------
# Client Input (WEBSOCKET_FIX_PLAN.md WS-10)
# ----------------------------------------------------------------------


@router.post("/session/{session_id}/input")
async def submit_session_input(
    session_id: str,
    request: SessionInputRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """Deliver user text to this session's planner input queue.

    This is the REST replacement for what used to arrive as a `user` /
    `user_input` / `chat_input` frame over the WebSocket -- the socket is
    push-only now (principle 1), so this is the only way in. Unlike the old
    WebSocket send, a client retrying this call (same `Idempotency-Key`) gets
    an acknowledgement instead of a silent drop when the socket happened to
    be closed (fixes W18): `ws_manager.push_to_queue` recognizes a repeated
    key and skips re-queuing it.
    """
    queued = await ws_manager.push_to_queue(
        request.text, session_id=session_id, idempotency_key=idempotency_key
    )
    return {"session_id": session_id, "status": "queued" if queued else "duplicate"}


# ----------------------------------------------------------------------
# Hypothesis History
# ----------------------------------------------------------------------


@router.get("/hypothesis-history")
async def get_hypothesis_history(limit: int = 20):
    """
    Fetch hypothesis history from the backend API.
    Returns list of recent hypotheses with their chat session info.
    """
    history = await session_service.get_hypothesis_history(limit)

    return history


# ----------------------------------------------------------------------
# Session Recovery
# ----------------------------------------------------------------------


@router.post("/session/{session_id}/recover")
async def recover_session(session_id: str, restart: bool = False):
    """Re-create an in-memory Session from persisted state after a process restart."""
    existing = session_service.get_session(session_id)
    if existing and existing.execution_task and not existing.execution_task.done():
        logger.info(
            f"[Recover] Session {session_id} is already running - skipping recovery."
        )
        return {
            "session_id": session_id,
            "status": "already_running",
            "state": existing.state.name,
        }

    session = await session_service.store.get_or_create_async(session_id)
    execution_state = await session_service.recover_execution_state(session_id)
    if execution_state is None:
        raise HTTPException(status_code=404, detail="No persisted session state found")

    if not restart:
        logger.info(
            f"[Recover] Hydrated session {session_id} as {session.state.name}; "
            "execution was not started."
        )
        return {
            "session_id": session_id,
            "status": "hydrated",
            "state": session.state.name,
            "execution_state": execution_state,
        }

    # Deferred: api.runners pulls in the full AutoGen/execution stack, which
    # this module otherwise has no reason to import just to define its
    # routes (websocket.py imports send_segmentation_catchup_snapshot from
    # here on every connect, and must stay importable without it).
    from api.runners import run_pipeline

    await session_service.start_execution(session_id, run_pipeline, restart=True)
    return {"session_id": session_id, "status": "restart_started"}
