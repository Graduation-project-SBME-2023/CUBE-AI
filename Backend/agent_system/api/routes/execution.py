"""Planning and pipeline execution control: plan, run, pause, resume, stop, status."""

import asyncio

from fastapi import APIRouter, HTTPException

from socket_store import manager as ws_manager
from models.requests import PlanningRequest, SessionControlRequest
from services.session_service import SessionExecutionConflict, SessionPlanningConflict
from clients.backend_client import BackendClient
from api.review_state import review_events, review_futures
from api.runners import run_pipeline, run_planner
from api.runtime import session_service

router = APIRouter()


@router.post("/plan")
async def start_planning(request: PlanningRequest):
    from functools import partial
    bound_planner = partial(run_planner, do_lit_review=request.do_lit_review)
    try:
        await session_service.start_planning(
            request.session_id,
            request.message,
            bound_planner,
            restart=request.restart,
        )
    except SessionPlanningConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"session_id": request.session_id, "status": "planning_started"}


# Both spellings are served explicitly. OpenResty strips the /agents prefix, so
# FastAPI's automatic trailing-slash redirect would answer /run/ with a Location
# of /run -- without the prefix the browser needs -- and the POST would land on
# the SPA's index.html instead of here. Accepting /run/ directly means no
# redirect is ever issued, so a cached older bundle still starts the pipeline.
@router.post("/run/", include_in_schema=False)
@router.post("/run")
async def start_execution(request: SessionControlRequest):
    """Approve the plan and execute it."""
    try:
        await session_service.start_execution(
            request.session_id,
            run_pipeline,
            restart=request.restart,
        )
    except SessionExecutionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    # Immediately tell the UI that execution has started so tabs unlock
    # without waiting for the first node_status message.
    await ws_manager.send_ws_message(
        "run_status",
        {"state": "RUNNING", "current_agent": None},
        session_id=request.session_id,
    )
    return {"session_id": request.session_id, "status": "execution_started"}


@router.post("/pause")
async def pause_execution(request: SessionControlRequest):
    try:
        session_service.pause(request.session_id)
        return {"session_id": request.session_id, "status": "paused"}
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@router.post("/resume")
async def resume_execution(request: SessionControlRequest):
    try:
        session_service.resume(request.session_id)
        return {"session_id": request.session_id, "status": "running"}
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@router.post("/session/{session_id}/resume-pipeline")
async def resume_hydrated_pipeline(session_id: str):
    """Continue an interrupted pipeline. **Deletes nothing**."""

    async def reset_runtime_state(
        target_session_id: str,
        interrupted_agent: str,
    ) -> None:
        if interrupted_agent != "segmentation_agent":
            return
        review_futures.pop(target_session_id, None)
        review_futures.pop(f"ready:{target_session_id}", None)
        review_events.pop(target_session_id, None)

    try:
        return await session_service.resume_pipeline(
            session_id,
            run_pipeline,
            reset_runtime_state=reset_runtime_state,
        )
    except SessionExecutionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/stop")
async def stop_execution(request: SessionControlRequest):
    try:
        session = session_service.store.require(request.session_id)

        session_service.stop(request.session_id)

        # Bug fix #1: broadcast run_stopped so the frontend resets its mode
        asyncio.create_task(
            ws_manager.send_ws_message("run_stopped", "stopped", session_id=request.session_id)
        )

        return {"session_id": request.session_id, "status": "stopped"}
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")



@router.get("/status/{session_id}")
async def get_status(session_id: str):
    session = session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session_id,
        "state": session.state.name,
        "last_error": session.last_error,
        "execution_active": bool(
            session.execution_task and not session.execution_task.done()
        ),
        # Per-step progress is durable now: it comes from `node_runs`, not
        # from what this process remembers.
        "runs": await BackendClient.get_node_status(session_id),
    }
