"""Hooks of ``latex_reporter_agent`` (see ``toolkit/agents/loader.py``).

- ``output_contract``: the published report the agent finishes with;
- ``completion_check``: that report is real: created by this run and
  compiled to the version the agent reports, and the output is recorded;
- ``extract_report``: the ``report`` output port.

A report starts at ``source_version`` 1 (``create_report``) and moves to the
next version only when ``latex_to_pdf`` compiles it (the publish is one
transaction), so a version of 2 or more means a PDF was produced.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..._runtime import ExtractedArtifact, ToolContext, domain_api

FIRST_PUBLISHED_VERSION = 2
AGENT_OUTPUT_STATUSES = ("completed", "partial")


class PublishedReport(BaseModel):
    """The final answer, checked by the engine before the agent may finish."""

    model_config = ConfigDict(extra="forbid")

    report_id: UUID
    source_version: int = Field(
        ge=FIRST_PUBLISHED_VERSION,
        description="The new_version of the last successful latex_to_pdf.",
    )
    title: str = Field(min_length=1)
    template: str = Field(min_length=1)
    figures: list[str] = Field(default_factory=list, description="The image_keys included.")
    warnings: list[str] = Field(default_factory=list)


output_contract = PublishedReport


async def completion_check(ctx: ToolContext, output: PublishedReport | None) -> list[str]:
    """What is still missing or wrong before the agent may finish.

    The ``report_id`` comes from the model, so it is held against the
    recorded row. An Application API that cannot be reached propagates and
    fails the attempt: the model cannot fix it.
    """
    if not ctx.run_id:
        return ["this agent is not running inside a node; there is no run to record on"]
    if output is None:
        return ["finish with the published report in the required structured format"]

    issues = []
    report = await domain_api.request(
        "GET", f"/reports/{domain_api.segment(output.report_id)}", allow_404=True
    )
    if report is None:
        issues.append(
            f"report {output.report_id} does not exist: report the report_id that create_report returned"
        )
    elif str(report.get("created_by_run_id")) != str(ctx.run_id):
        issues.append(
            f"report {output.report_id} was not created by this run: report the report_id "
            "that your own create_report call returned"
        )
    elif report.get("source_version", 1) < FIRST_PUBLISHED_VERSION:
        issues.append(
            "the report has no PDF yet: fix the LaTeX and call latex_to_pdf until it succeeds"
        )
    elif report["source_version"] != output.source_version:
        issues.append(
            f"the report is at version {report['source_version']}, not {output.source_version}: "
            "report the new_version of your last successful latex_to_pdf"
        )

    run = await domain_api.get_run(str(ctx.run_id))
    agent_output = (run.get("scratchpad_summary") or {}).get("agent_output") or {}
    if agent_output.get("status") not in AGENT_OUTPUT_STATUSES:
        issues.append(
            "your structured output is not recorded: call write_agent_output with "
            "agent_id='latex_reporter_agent' and status 'completed'"
        )
    return issues


def extract_report(result: Any) -> ExtractedArtifact:
    """The ``ReportDocument`` the agent published, from its checked output.

    Called by the Node Runner after the completion check passed, so the
    report exists, belongs to this run and is at this version.
    """
    report = PublishedReport.model_validate(result.output)
    return ExtractedArtifact(
        ref={"report_id": str(report.report_id)},
        summary={"source_version": report.source_version, "has_pdf": True},
    )
