import json

from .._toolkit import error_result, tool
from ..common.manifests import WRITES
from ..common.emit import emit, emit_view
from ..common.polling import PollTimeout, poll_until
from ..common.settings import positive_float
from ..bookkeeping.scratchpad import scratchpad_raise_flag, scratchpad_write_note
from ..common import domain_api

DEFAULT_APPROVAL_POLL_S = 2.0
APPROVAL_TIMEOUT_S = 3600.0

# The sessions whose UI was already told the gate is waiting, so a second call
# in the same session does not announce it again (L7: was one global flag,
# which one session's wait set for every other session).
_announced_sessions: set[str] = set()


def _is_transient(exc: BaseException) -> bool:
    """A blip of the Application API must not end the wait (S12)."""
    return isinstance(exc, domain_api.DomainApiError) and exc.error_code in (
        "unavailable",
        "upstream_error",
    )


async def _wait_for_cohort_approval(timeout_seconds: float = APPROVAL_TIMEOUT_S) -> dict:
    """Wait until the doctor approves (freezes) the population cohort.
    Raises ``PollTimeout`` after ``timeout_seconds``."""

    async def approved_cohort() -> dict | None:
        cohort = await domain_api.get_population_cohort()
        if cohort and cohort.get("frozen_at"):
            return cohort
        return None

    session = domain_api.session_id()
    cohort = await approved_cohort()
    if cohort is not None:
        _announced_sessions.discard(session)
        return cohort

    if session not in _announced_sessions:
        _announced_sessions.add(session)
        emit("tool_log", "Waiting for doctor to review and approve final cohort in UI...")
        await emit_view(
            "agent_waiting_for_user_input",
            {
                "agent": "study_population_planner_agent",
                "status": "WAITING_FOR_REVIEW",
            },
        )
    try:
        return await poll_until(
            approved_cohort,
            interval_s=positive_float("COHORT_APPROVAL_POLL_SECONDS", DEFAULT_APPROVAL_POLL_S),
            timeout_s=timeout_seconds,
            is_transient=_is_transient,
        )
    finally:
        _announced_sessions.discard(session)


@tool(
    visibility={"agent"},
    data_scopes={"phi_read", "db_write"},
    timeout_s=3660,
    **WRITES,
)
async def generate_targets() -> str:
    """Prepares the cohort's target labels for review and acts as the Human-in-the-Loop approval gate before segmentation."""
    emit('tool_log', 'preparing targets & awaiting cohort approval')
    try:
        return await _generate_targets()
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc)
    except PollTimeout:
        return error_result(
            "timeout",
            "Cohort approval timed out waiting for user review in UI. "
            "Pipeline cannot continue without approval.",
        )


async def _generate_targets() -> str | dict:
    cohort = await domain_api.require_population_cohort()

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

    approved = await _wait_for_cohort_approval()
    rows = await domain_api.cohort_analysis_rows(approved["cohort_id"])
    final_count = len(rows)

    if final_count <= 0:
        return error_result(
            "empty_cohort",
            "Cohort approval produced zero labeled volumes. The population stage "
            "cannot continue until at least one volume is selected and labeled.",
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

    emit('tool_log', f"Cohort Approved & Targets Locked! ({final_count} volumes)")
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
