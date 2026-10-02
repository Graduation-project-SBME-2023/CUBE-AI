import os
import json
import asyncio

from .._toolkit import tool
from ..common.manifests import WRITES
from ..common.emit import ToolEvent, log_tool_state, TextPayload
from ..bookkeeping.scratchpad import scratchpad_raise_flag, scratchpad_write_note
from ..common import domain_api
from ..common.emit import broadcast_ws_message

APPROVAL_POLL_SECONDS = float(os.environ.get("COHORT_APPROVAL_POLL_SECONDS", "2"))

_IS_WAITING_FOR_APPROVAL = False


async def _wait_for_cohort_approval(timeout_seconds: int = 3600) -> dict:
    """Wait until the doctor approves (freezes) the population cohort."""
    global _IS_WAITING_FOR_APPROVAL

    async def approved_cohort() -> dict | None:
        cohort = await domain_api.get_population_cohort()
        if cohort and cohort.get("frozen_at"):
            return cohort
        return None

    cohort = await approved_cohort()
    if cohort is not None:
        _IS_WAITING_FOR_APPROVAL = False
        return cohort

    if not _IS_WAITING_FOR_APPROVAL:
        _IS_WAITING_FOR_APPROVAL = True
        payload = TextPayload(message="Waiting for doctor to review and approve final cohort in UI...")
        log_tool_state(ToolEvent(event_type='tool_started', content_type='text', content=payload))
        session_id = os.environ.get("SESSION_ID")
        if session_id:
            await broadcast_ws_message(
                "agent_waiting_for_user_input",
                {
                    "agent": "study_population_planner_agent",
                    "status": "WAITING_FOR_REVIEW",
                },
                session_id,
            )

    elapsed = 0.0
    while True:
        await asyncio.sleep(APPROVAL_POLL_SECONDS)
        elapsed += APPROVAL_POLL_SECONDS
        cohort = await approved_cohort()
        if cohort is not None:
            _IS_WAITING_FOR_APPROVAL = False
            return cohort
        if elapsed >= timeout_seconds:
            _IS_WAITING_FOR_APPROVAL = False
            raise RuntimeError(
                "Execution stopped: Cohort approval timed out waiting for user review in UI. "
                "Pipeline cannot continue without approval."
            )


@tool(
    visibility={"agent"},
    **WRITES,
)
async def generate_targets() -> str:
    """Prepares the cohort's target labels for review and acts as the Human-in-the-Loop approval gate before segmentation."""
    log_tool_state(ToolEvent(event_type='tool_started', content_type='text',
                             content=TextPayload(message='preparing targets & awaiting cohort approval')))

    try:
        cohort = await domain_api.require_population_cohort()
    except domain_api.DomainApiError as exc:
        return json.dumps({"status": "error", "message": str(exc)})

    members = cohort.get("members") or []
    labelling_mode = (cohort.get("criteria") or {}).get("labelling_mode", "automatic")
    if not members:
        await scratchpad_raise_flag(
            level="warning",
            message=(
                "The cohort is empty (0 volumes). Waiting at the Human-in-the-Loop "
                "checkpoint until the user manually selects and labels at least one volume. "
                "Downstream execution is blocked for an empty cohort."
            ),
        )
    elif not cohort.get("frozen_at"):
        field = (cohort.get("label_definition") or {}).get("field", "has_target_disease")
        unlabeled = [
            member["volume_series_instance_uid"]
            for member in members
            if not isinstance((member.get("label") or {}).get(field), bool)
        ]
        low_confidence = [
            member["volume_series_instance_uid"]
            for member in members
            if member.get("label_confidence") == "low"
        ]
        if unlabeled:
            await scratchpad_raise_flag(
                level="warning",
                message=(
                    f"{len(unlabeled)} of {len(members)} cohort volume(s) have no valid label "
                    f"({labelling_mode} mode). They stay unlabelled — and out of the analysis — "
                    "unless the doctor labels them before approving."
                ),
            )
        if low_confidence:
            await scratchpad_raise_flag(
                level="warning",
                message=(
                    f"Included {len(low_confidence)} volume(s) with low LLM confidence "
                    "(retained as per policy)."
                ),
            )

    approved = await _wait_for_cohort_approval(timeout_seconds=3600)
    rows = await domain_api.cohort_analysis_rows(approved["cohort_id"])
    final_count = len(rows)

    if final_count <= 0:
        raise RuntimeError(
            "Cohort approval produced zero labeled volumes. The population stage "
            "cannot continue until at least one volume is selected and labeled."
        )
    await scratchpad_write_note(
        content=(
            "### Validated Study Cohort Approved by Doctor\n"
            f"The user has reviewed and approved (locked) the final cohort in the UI with "
            f"{final_count} labelled volume(s). Even if preliminary automated searches yielded "
            f"fewer or zero cases, this post-review count ({final_count}) is the authoritative "
            f"cohort size. Downstream agents read it from cohort {approved['cohort_id']}."
        )
    )

    log_tool_state(ToolEvent(event_type='tool_finished', content_type='text',
                             content=TextPayload(message=f"Cohort Approved & Targets Locked! ({final_count} volumes)")))
    return json.dumps({
        "status": "success",
        "cohort_id": approved["cohort_id"],
        "final_cohort_size": final_count,
        "message": (
            f"Human-in-the-Loop review complete and cohort approved in the UI. Final validated "
            f"cohort size after user review and manual labeling is exactly {final_count} volumes. "
            f"You MUST report this exact final cohort size ({final_count}) and cohort_id "
            f"{approved['cohort_id']} in your final summary and write_agent_output calls."
        ),
    })
