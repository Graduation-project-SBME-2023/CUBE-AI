"""Durable completion contract for the study population pipeline stage."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

POPULATION_AGENT_ID = "study_population_planner_agent"

RunFetcher = Callable[[str], Awaitable[dict[str, Any] | None]]
CohortFetcher = Callable[[str], Awaitable[dict[str, Any] | None]]


async def _default_run(agent_run_id: str) -> dict[str, Any] | None:
    from clients.backend_client import BackendClient

    return await BackendClient.get_agent_run(agent_run_id)


async def _default_cohort(session_id: str) -> dict[str, Any] | None:
    from clients.backend_client import BackendClient

    return await BackendClient.get_population_cohort(session_id)


def _labelled_count(cohort: dict[str, Any]) -> int:
    field = (cohort.get("label_definition") or {}).get("field", "has_target_disease")
    return sum(
        1
        for member in cohort.get("members") or []
        if isinstance((member.get("label") or {}).get(field), bool)
    )


async def population_handoff_issues(
    session_id: str,
    agent_run_id: str | None,
    *,
    fetch_run: RunFetcher | None = None,
    fetch_cohort: CohortFetcher | None = None,
) -> list[str]:
    """Return every missing/invalid record that makes handoff unsafe."""
    fetch_run = fetch_run or _default_run
    fetch_cohort = fetch_cohort or _default_cohort
    issues: list[str] = []

    if not agent_run_id:
        return ["the population step has no agent run; nothing can be verified"]

    cohort = await fetch_cohort(session_id)
    if not cohort:
        issues.append("the population cohort has not been recorded")
    else:
        validation = (cohort.get("selection_summary") or {}).get("anatomy_validation") or {}
        if validation.get("anatomy_validated") is not True:
            issues.append("validate_population_anatomy has not completed")
        if not cohort.get("members"):
            issues.append("validated cohort is empty; at least one cohort volume is required")
        if not cohort.get("frozen_at"):
            issues.append("doctor cohort approval has not completed")
        elif _labelled_count(cohort) == 0:
            issues.append("the approved cohort has no labelled volume")

    run = await fetch_run(agent_run_id)
    if not run:
        issues.append("the population agent run could not be read")
        return issues
    notes = run.get("durable_notes") or {}
    if notes.get("status") != "done":
        issues.append("population scratchpad status is not done")
    summary = (run.get("output_summary") or {}).get("scratchpad_summary")
    if not isinstance(summary, dict) or not str(summary.get("content", "")).strip():
        issues.append("population scratchpad summary is missing")
    agent_output = (run.get("output_summary") or {}).get("agent_output")
    if not isinstance(agent_output, dict):
        issues.append("population structured output is missing")
    elif agent_output.get("status") != "completed":
        issues.append("population structured output status is not completed")
    return issues


async def require_population_handoff(
    session_id: str,
    agent_run_id: str | None,
    **kwargs: Any,
) -> None:
    """Raise when the population agent has not completed its durable contract."""
    issues = await population_handoff_issues(session_id, agent_run_id, **kwargs)
    if issues:
        raise RuntimeError("Population handoff is incomplete: " + "; ".join(issues))
