"""Hooks of ``result_reporter_agent`` (see ``toolkit/agents/loader.py``).

- ``output_contract``: the judgement the agent finishes with;
- ``completion_check``: the agent may finish only once its judgement is on
  its run, where the ``ResultSummary`` ref points and the report writer reads it;
- ``extract_summary``: the ``summary`` output port.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..._runtime import ExtractedArtifact, ToolContext, current_context, domain_api

AGENT_OUTPUT_STATUSES = ("completed", "partial")


class ResultFindings(BaseModel):
    """The final judgement, checked by the engine before the agent may finish."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["accepted", "rejected", "inconclusive"]
    justification: str = Field(min_length=1)
    key_findings: list[str] = Field(min_length=1)
    primary_metric: str | None = None
    primary_metric_value: float | None = None
    confidence: Literal["high", "medium", "low"]
    limitations: list[str] = Field(default_factory=list)


output_contract = ResultFindings


async def completion_check(ctx: ToolContext, output: ResultFindings | None) -> list[str]:
    """What is still missing on this run before the agent may finish.

    An Application API that cannot be reached is not something the agent can
    fix, so that error propagates and fails the attempt instead of becoming
    an instruction to the model.
    """
    if not ctx.run_id:
        return ["this agent is not running inside a node; there is no run to record on"]
    run = await domain_api.get_run(str(ctx.run_id))
    recorded = run.get("scratchpad_summary") or {}

    issues = []
    summary = recorded.get("scratchpad_summary") or {}
    if not str(summary.get("content", "")).strip():
        issues.append(
            "your judgement is not recorded: call scratchpad_write_summary with the decision, "
            "its justification, the key evidence and the limitations"
        )
    agent_output = recorded.get("agent_output") or {}
    if agent_output.get("status") not in AGENT_OUTPUT_STATUSES:
        issues.append(
            "your structured output is not recorded: call write_agent_output with "
            "agent_id='result_reporter_agent' and status 'completed' (or 'partial')"
        )
    return issues


def extract_summary(result: Any) -> ExtractedArtifact:
    """The ``ResultSummary``: this run (whose scratchpad summary holds the
    findings) and the number of findings.

    Called by the Node Runner inside the node's context, after the engine has
    checked ``output_contract``; ``result`` is the ``AgentResult``.
    """
    findings = ResultFindings.model_validate(result.output)
    run_id = current_context().run_id
    if not run_id:
        raise ValueError("extract_summary needs the node's run in the context")
    return ExtractedArtifact(
        ref={"run_id": str(run_id)},
        summary={"n_findings": len(findings.key_findings)},
    )
