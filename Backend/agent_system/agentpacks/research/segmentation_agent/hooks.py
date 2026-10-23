"""Hooks of ``segmentation_agent`` (see ``toolkit/agents/loader.py``).

- ``output_contract``: the reviewed batch the agent finishes with;
- ``completion_check``: the batch is this run's and reviewed, and the counts
  the agent reports are the ones ``run_segmentation_with_guard`` recorded on
  the run (the tool, not the model, writes this step's output);
- ``extract_masks``: the ``masks`` output port.

The agent is thin on purpose: the clinical decisions happen inside the tool,
through the two internal agents. It exists because only an agent node can
suspend on the ``segmentation_review`` gate (Phase 9.3).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..._runtime import ExtractedArtifact, ToolContext, domain_api

REVIEWED = "submitted"
TOOL_OUTPUT_STATUSES = ("completed", "partial")


class SegmentedMasks(BaseModel):
    """The final answer, checked by the engine before the agent may finish."""

    model_config = ConfigDict(extra="forbid")

    segmentation_batch_id: UUID
    n_masks: int = Field(ge=0, description="The accepted masks (accepted_count).")
    n_failed: int = Field(ge=0, description="The volumes that failed.")
    review_status: Literal["not_ready", "pending", "submitting", "submitted"]
    warnings: list[str] = Field(default_factory=list)


output_contract = SegmentedMasks


def _recorded_failures(run: dict[str, Any], agent_output: dict[str, Any]) -> int:
    """The failures the tool recorded: as errors of a failed batch, or as the
    warnings its output added to the run (``source: agent_output``)."""
    warnings = (run.get("scratchpad_warnings") or {}).get("items", [])
    return len(agent_output.get("errors") or []) + sum(
        1 for item in warnings if item.get("source") == "agent_output"
    )


async def completion_check(ctx: ToolContext, output: SegmentedMasks | None) -> list[str]:
    """What is missing or wrong before the agent may finish. An unreachable
    Application API propagates and fails the attempt."""
    if not ctx.run_id:
        return ["this agent is not running inside a node; there is no run to record on"]
    if output is None:
        return ["finish with the reviewed batch in the required structured format"]

    run_id = str(ctx.run_id)
    run = await domain_api.get_run(run_id)
    agent_output = (run.get("scratchpad_summary") or {}).get("agent_output") or {}
    if agent_output.get("status") not in TOOL_OUTPUT_STATUSES:
        return [
            "no segmentation is recorded on this run: call run_segmentation_with_guard "
            "and report its result"
        ]

    issues = []
    outputs = agent_output.get("outputs") or {}
    recorded_batch = str((outputs.get("files") or {}).get("segmentation_batch_id"))
    if recorded_batch != str(output.segmentation_batch_id):
        issues.append(
            f"this run's segmentation recorded batch {recorded_batch}, not "
            f"{output.segmentation_batch_id}: report the tool's segmentation_batch_id"
        )
    accepted = (outputs.get("metrics") or {}).get("accepted_count")
    if accepted != output.n_masks:
        issues.append(f"the tool accepted {accepted} masks, not {output.n_masks}: report its count")
    failures = _recorded_failures(run, agent_output)
    if failures != output.n_failed:
        issues.append(f"the tool recorded {failures} failures, not {output.n_failed}: report them")

    batch = await domain_api.request(
        "GET",
        f"/segmentation-batches/{domain_api.segment(output.segmentation_batch_id)}",
        allow_404=True,
    )
    if batch is None or str(batch.get("created_by_run_id")) != run_id:
        issues.append(
            f"batch {output.segmentation_batch_id} is not a batch of this run: report the "
            "segmentation_batch_id run_segmentation_with_guard returned"
        )
    elif batch.get("review_status") != output.review_status:
        issues.append(
            f"the batch's review status is {batch.get('review_status')}, not "
            f"{output.review_status}: report the recorded status"
        )
    elif output.review_status != REVIEWED:
        issues.append(
            "the masks are not reviewed yet, so no later step may use them: report the "
            "failure with scratchpad_raise_flag"
        )
    if output.n_masks == 0:
        issues.append("no mask was accepted: raise an error flag; there is nothing to measure")
    return issues


def extract_masks(result: Any) -> ExtractedArtifact:
    """The ``MaskSet`` of the reviewed batch, from the checked output."""
    output = SegmentedMasks.model_validate(result.output)
    return ExtractedArtifact(
        ref={"segmentation_batch_id": str(output.segmentation_batch_id)},
        summary={
            "n_masks": output.n_masks,
            "n_failed": output.n_failed,
            "review_status": output.review_status,
        },
    )
