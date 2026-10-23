"""Hooks of ``study_population_planner_agent`` (see ``toolkit/agents/loader.py``).

- ``output_contract``: the frozen cohort the agent finishes with;
- ``completion_check``: the durable handoff of ``services/population_handoff.py``
  (copied, the legacy module stays with the frozen pipeline), on the new
  runtime: the cohort is this run's, anatomy-validated, frozen and
  labelled, at the size the agent reports. The legacy ``status == done``
  check is gone: the Node Runner owns the run's status now;
- ``extract_cohort``: the ``cohort`` output port.

``extract_cohort_labels`` is in the allowlist whatever the labelling mode:
the tool itself refuses outside automatic mode (``manual_labelling``), so the
rule holds for every caller, not only for this agent's allowlist.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..._runtime import ExtractedArtifact, ToolContext, domain_api

AGENT_OUTPUT_STATUSES = ("completed",)


class FrozenCohort(BaseModel):
    """The final answer, checked by the engine before the agent may finish."""

    model_config = ConfigDict(extra="forbid")

    cohort_id: UUID
    cohort_size: int = Field(ge=1, description="final_cohort_size from generate_targets")
    modality: str = Field(min_length=1)
    inclusion_criteria: list[str] = Field(min_length=1)
    exclusion_criteria: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


output_contract = FrozenCohort


async def completion_check(ctx: ToolContext, output: FrozenCohort | None) -> list[str]:
    """Every missing or invalid record that makes the handoff unsafe. An
    unreachable Application API propagates and fails the attempt."""
    if not ctx.run_id:
        return ["this agent is not running inside a node; there is no run to record on"]
    if output is None:
        return ["finish with the frozen cohort in the required structured format"]

    run_id = str(ctx.run_id)
    issues = []
    cohort = await domain_api.get_population_cohort(str(ctx.session_id))
    if not cohort or str(cohort.get("created_by_run_id")) != run_id:
        issues.append(
            "this run has recorded no population cohort: call write_inclusion_criteria, "
            "then search and validate the volumes"
        )
    else:
        issues.extend(await _cohort_issues(cohort, output))

    run = await domain_api.get_run(run_id)
    recorded = run.get("scratchpad_summary") or {}
    if not str((recorded.get("scratchpad_summary") or {}).get("content", "")).strip():
        issues.append("the population summary is missing: call scratchpad_write_summary")
    if (recorded.get("agent_output") or {}).get("status") not in AGENT_OUTPUT_STATUSES:
        issues.append(
            "the structured output is missing: call write_agent_output with "
            "agent_id='study_population_planner_agent' and status 'completed'"
        )
    return issues


async def _cohort_issues(cohort: dict[str, Any], output: FrozenCohort) -> list[str]:
    issues = []
    if str(cohort.get("cohort_id")) != str(output.cohort_id):
        issues.append(
            f"this run's cohort is {cohort.get('cohort_id')}, not {output.cohort_id}: "
            "report the cohort_id generate_targets returned"
        )
    validation = (cohort.get("selection_summary") or {}).get("anatomy_validation") or {}
    if validation.get("anatomy_validated") is not True:
        issues.append(
            "validate_population_anatomy has not completed: call it with the exact "
            "volumes_detail of the latest search"
        )
    if not cohort.get("members"):
        issues.append("the cohort is empty: at least one volume is required")
    if not cohort.get("frozen_at"):
        issues.append(
            "the cohort is not frozen: call generate_targets (after ask_user when the "
            "user asked to approve it)"
        )
        return issues
    labelled = len(await domain_api.cohort_analysis_rows(str(cohort["cohort_id"])))
    if labelled == 0:
        issues.append("the frozen cohort has no labelled volume")
    elif labelled != output.cohort_size:
        issues.append(
            f"the frozen cohort has {labelled} labelled volumes, not {output.cohort_size}: "
            "report the final_cohort_size generate_targets returned"
        )
    return issues


def extract_cohort(result: Any) -> ExtractedArtifact:
    """The frozen ``Cohort``, from the checked output. The label counts are
    not the model's to report, so the summary leaves them out."""
    output = FrozenCohort.model_validate(result.output)
    return ExtractedArtifact(
        ref={"cohort_id": str(output.cohort_id)},
        summary={"size": output.cohort_size, "frozen": True},
    )
