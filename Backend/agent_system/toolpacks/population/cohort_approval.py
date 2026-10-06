"""``generate_targets``: the Human-in-the-Loop gate of the population step.

It flags what the doctor should look at (an empty cohort, unlabelled or
low-confidence volumes), waits until the doctor approves (freezes) the cohort
in the UI, and reports the approved size. Nothing downstream may start on a
cohort that was not approved, or on an empty one.
"""

from __future__ import annotations

import json
from typing import Any

from .._toolkit import error_result, tool
from ..bookkeeping.scratchpad import raise_flag, write_note
from ..common import domain_api, views
from ..common.emit import emit, emit_view
from ..common.manifests import WRITES
from ..common.polling import PollTimeout, poll_until
from ..common.settings import positive_float

DEFAULT_APPROVAL_POLL_S = 2.0
APPROVAL_TIMEOUT_S = 3600.0
POPULATION_AGENT = "study_population_planner_agent"

# The sessions whose UI was already told the gate is waiting, so a second call
# in the same session does not announce it again (L7: was one global flag,
# which one session's wait set for every other session).
_announced_sessions: set[str] = set()


def _is_transient(exc: BaseException) -> bool:
    """A blip of the Application API must not end the wait (S12)."""
    return isinstance(exc, domain_api.DomainApiError) and exc.error_code in ("unavailable", "upstream_error")


async def _approved_cohort() -> dict[str, Any] | None:
    cohort = await domain_api.get_population_cohort()
    return cohort if cohort and cohort.get("frozen_at") else None


async def _announce_wait(session: str) -> None:
    if session in _announced_sessions:
        return
    _announced_sessions.add(session)
    emit("tool_log", "Waiting for doctor to review and approve final cohort in UI...")
    await emit_view(
        "agent_waiting_for_user_input",
        {"agent": POPULATION_AGENT, "status": "WAITING_FOR_REVIEW"},
    )


async def _wait_for_cohort_approval(timeout_seconds: float = APPROVAL_TIMEOUT_S) -> dict[str, Any]:
    """The cohort once the doctor approved it. Raises ``PollTimeout`` after ``timeout_seconds``."""
    session = domain_api.session_id()
    try:
        cohort = await _approved_cohort()
        if cohort is not None:
            return cohort
        await _announce_wait(session)
        return await poll_until(
            _approved_cohort,
            interval_s=positive_float("COHORT_APPROVAL_POLL_SECONDS", DEFAULT_APPROVAL_POLL_S),
            timeout_s=timeout_seconds,
            is_transient=_is_transient,
        )
    finally:
        _announced_sessions.discard(session)


async def _flag_review_points(cohort: dict[str, Any]) -> None:
    """Scratchpad flags for what the doctor should look at before approving."""
    members = cohort.get("members") or []
    if not members:
        await raise_flag(
            "warning",
            "The cohort is empty (0 volumes). Waiting at the Human-in-the-Loop checkpoint until "
            "the user manually selects and labels at least one volume. Downstream execution is "
            "blocked for an empty cohort.",
        )
        return
    if cohort.get("frozen_at"):
        return
    labelling_mode = (cohort.get("criteria") or {}).get("labelling_mode", "automatic")
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
        await raise_flag(
            "warning",
            f"{len(unlabeled)} of {len(members)} cohort volume(s) have no valid label "
            f"({labelling_mode} mode). They stay unlabelled — and out of the analysis — "
            "unless the doctor labels them before approving.",
        )
    if low_confidence:
        await raise_flag(
            "warning",
            f"Included {len(low_confidence)} volume(s) with low LLM confidence (retained as per policy).",
        )


async def _generate_targets() -> str | dict[str, Any]:
    await _flag_review_points(await domain_api.require_population_cohort())
    approved = await _wait_for_cohort_approval()
    final_count = len(await domain_api.cohort_analysis_rows(approved["cohort_id"]))
    if final_count <= 0:
        return error_result(
            "empty_cohort",
            "Cohort approval produced zero labeled volumes. The population stage "
            "cannot continue until at least one volume is selected and labeled.",
        )
    await write_note(
        "### Validated Study Cohort Approved by Doctor\n"
        f"The user has reviewed and approved (locked) the final cohort in the UI with "
        f"{final_count} labelled volume(s). Even if preliminary automated searches yielded "
        f"fewer or zero cases, this post-review count ({final_count}) is the authoritative "
        f"cohort size. Downstream agents read it from cohort {approved['cohort_id']}."
    )
    emit("tool_log", f"Cohort Approved & Targets Locked! ({final_count} volumes)")
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


@tool(
    visibility={"agent"},
    data_scopes={"phi_read", "db_write"},
    timeout_s=APPROVAL_TIMEOUT_S + 60,
    **WRITES,
    on_result=views.on_population_tool,
)
async def generate_targets() -> str | dict[str, Any]:
    """Prepares the cohort's target labels for review and acts as the Human-in-the-Loop approval gate before segmentation."""
    emit("tool_log", "preparing targets & awaiting cohort approval")
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
