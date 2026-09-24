"""Session replay, recovery, and history endpoints."""

import logging
import os

import httpx
from fastapi import APIRouter, HTTPException

from storage_paths import workspace
from socket_store import manager as ws_manager
from models.logs import WS_Log
from services import population_review
from clients.backend_client import BackendClient
from agents.execution_agents.segmentation.progressive import build_snapshot_from_manifest
from api.runners import run_pipeline
from api.runtime import session_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ----------------------------------------------------------------------
# State Recovery
# ----------------------------------------------------------------------

@router.get("/session/{session_id}/replay-logs")
async def replay_logs(session_id: str, after_event_id: int = 0):
    """Replay a session's events from a cursor."""
    logs: list[WS_Log] = await BackendClient.replay_logs(
        session_id, after_event_id=after_event_id
    )
    last_event_id = after_event_id
    for log in logs:
        last_event_id = log.event_id or last_event_id
        if log.msg_type in ('msg'):
            continue
        await ws_manager.send_ws_message(
            log.msg_type,
            log.payload,
            log.session_id,
            replaying=True,
            event_id=log.event_id,
        )

    if after_event_id == 0 and not logs:
        # Nothing has ever happened in this session (fresh create/open) — no
        # agent has run yet, so there is no segmentation batch to resume a
        # review for. Skip the DB round trip entirely rather than always
        # probing it on every session open.
        return

    workspace_dir = str(workspace(session_id))
    try:
        batch = await population_review.current_batch(session_id)
        if batch and batch["review_status"] != "not_ready":
            # current this flow: rebuilt from segmentation_runs/pairs, including the
            # doctor's draft decisions, which are rows now.
            manifest = population_review.snapshot_manifest(batch)
            snapshot = build_snapshot_from_manifest(
                manifest,
                workspace_dir,
                os.environ.get("MAS_BASE_URL", "http://localhost:8150"),
            )
            if snapshot:
                await ws_manager.send_ws_message(
                    "segmentation_output",
                    snapshot,
                    session_id,
                    replaying=True,
                )
    except (population_review.DomainError, httpx.HTTPError, ValueError, KeyError):
        logger.warning(
            "Could not reconstruct segmentation snapshot for session %s",
            session_id,
            exc_info=True,
        )



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
                f"{monai_url}/chat_messages/{session_id}", timeout=10.0
            )
            if response.status_code == 200:
                return response.json()
            else:
                return []
        except Exception as e:
            logger.warning(f"Could not fetch chat messages: {e}")
            return []


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

    await session_service.start_execution(session_id, run_pipeline, restart=True)
    return {"session_id": session_id, "status": "restart_started"}
