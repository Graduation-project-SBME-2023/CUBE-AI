"""Cohort management endpoints for the interactive Population subpages."""

import logging

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from socket_store import manager as ws_manager
from services import population_review
from api.runtime import domain_http_error, session_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ----------------------------------------------------------------------
# Population cohort endpoints (Interactive Subpages)
# ----------------------------------------------------------------------

@router.post("/populations/set-labelling-mode")
async def set_labelling_mode(
    session_id: str = Query(..., description="The ID of the current session"),
    mode: str = Query(..., description="Labelling mode: 'manual' or 'automatic'")
):
    if mode not in ["manual", "automatic"]:
        raise HTTPException(status_code=400, detail="mode must be 'manual' or 'automatic'")
    try:
        await population_review.ensure_cohort(session_id, criteria={"labelling_mode": mode})
    except population_review.DomainError as exc:
        raise domain_http_error(exc)
    return {"status": "success", "mode": mode}


@router.get("/populations/cohort-state")
async def get_cohort_state(
    session_id: str = Query(..., description="The ID of the current session"),
):
    """The Population subpages' state, assembled from the cohort rows."""
    try:
        cohort = await population_review.get_cohort(session_id)
    except population_review.DomainError as exc:
        raise domain_http_error(exc)
    return population_review.cohort_state(cohort)


@router.post("/populations/update-cohort-list")
async def update_cohort_list(data: dict):
    """Replace the draft cohort membership from the Data Browser subpage."""
    session_id = data.get("session_id")
    new_datasetlist = [str(uid) for uid in data.get("datasetlist", []) or []]
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    try:
        cohort = await population_review.ensure_cohort(session_id)
        cohort = await population_review._api(
            "PUT",
            f"/cohorts/{cohort['cohort_id']}/members",
            json={"volume_series_instance_uids": new_datasetlist},
        )
    except population_review.DomainError as exc:
        raise domain_http_error(exc)

    try:
        await ws_manager.send_ws_message(
            "population_output",
            {
                "tool": "search_volumes_by_description",
                "status": "completed",
                "count": len(new_datasetlist),
                "uid_list": new_datasetlist,
            },
            session_id=session_id,
        )
    except Exception as e:
        logger.warning(f"Failed to broadcast cohort list update: {e}")
    return {"status": "success", "count": len(cohort.get("members") or [])}


@router.post("/populations/save-labels")
async def save_labels(data: dict):
    """Record the doctor's labels from the Patient Labeling subpage."""
    session_id = data.get("session_id")
    labels_list = data.get("labels", []) or []
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    try:
        cohort = await population_review.get_cohort(session_id)
        if cohort is None:
            raise HTTPException(status_code=409, detail="The population cohort has not been defined yet")
        writes = population_review.manual_label_writes(cohort, labels_list)
        if writes:
            await population_review._api(
                "PATCH", f"/cohorts/{cohort['cohort_id']}/labels", json={"labels": writes}
            )
    except population_review.DomainError as exc:
        raise domain_http_error(exc)
    return {"status": "success", "saved_count": len(writes)}


@router.post("/populations/approve-cohort")
async def approve_cohort(data: dict):
    """The doctor's approval — freezes the cohort (one-way)."""
    session_id = data.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    try:
        cohort = await population_review.get_cohort(session_id)
        if cohort is None:
            raise HTTPException(
                status_code=400,
                detail="Cannot approve: no volumes in the cohort. The population agent must discover volumes first.",
            )
        result = await population_review._api("POST", f"/cohorts/{cohort['cohort_id']}/freeze")
    except population_review.DomainError as exc:
        raise domain_http_error(exc)

    event = result.get("event")
    if event:
        # Broadcast the event PostgreSQL already committed, with its cursor.
        await ws_manager.send_ws_message(
            event["msg_type"],
            event["payload"],
            session_id=session_id,
            event_id=event["event_id"],
        )
    logger.info(
        "[CohortApproval] Session %s: approved %s labeled volumes out of %s total.",
        session_id,
        result["labeled_count"],
        result["total_count"],
    )
    return {
        "status": "success",
        "labeled_count": result["labeled_count"],
        "cohort_id": cohort["cohort_id"],
    }


@router.post("/populations/upload-targets")
async def upload_targets(
    session_id: str = Query(..., description="The ID of the current session"),
    file: UploadFile = File(...)
):
    """Labels from a doctor-uploaded targets file (read, never stored)."""
    try:
        cohort = await population_review.get_cohort(session_id)
        if cohort is None:
            raise HTTPException(status_code=409, detail="The population cohort has not been defined yet")
        writes, unmatched = population_review.uploaded_target_writes(cohort, await file.read())
        if writes:
            await population_review._api(
                "PATCH", f"/cohorts/{cohort['cohort_id']}/labels", json={"labels": writes}
            )
    except population_review.DomainError as exc:
        raise domain_http_error(exc)

    active_session = session_service.get_session(session_id)
    if (
        active_session
        and active_session.execution_task
        and not active_session.execution_task.done()
    ):
        await session_service.persist_agent_status(
            session_id,
            "study_population_planner_agent",
            "RUNNING",
        )
    await ws_manager.send_ws_message(
        "agent_waiting_resolved",
        {"agent": "study_population_planner_agent"},
        session_id=session_id,
    )
    return {
        "message": "Targets recorded on the cohort",
        "session_id": session_id,
        "labeled_count": sum(1 for item in writes if item["has_target_disease"] is not None),
        "unmatched_ids": unmatched[:100],
    }
