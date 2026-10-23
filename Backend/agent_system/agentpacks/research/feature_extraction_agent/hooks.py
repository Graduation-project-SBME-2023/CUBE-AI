"""Hooks of ``feature_extraction_agent`` (see ``toolkit/agents/loader.py``).

- ``output_contract``: the frozen feature sets the agent finishes with;
- ``completion_check``: every set it reports is a frozen set of this run with
  the counts it reports, no frozen set of this run is left out, and the
  output is recorded;
- ``extract_features`` / ``extract_importance``: the two output ports.

``radiomics_analyze_importance`` freezes several sets (``top_<n>`` and
``all_features``); ``run_modeling`` takes them all (``FeatureTable[]``,
Phase 7.3), so ``features`` is a many port. The importance ranking ranks every
feature, so its table is the ``all_features`` set.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..._runtime import ExtractedArtifact, ToolContext, domain_api

ALL_FEATURES = "all_features"
AGENT_OUTPUT_STATUSES = ("completed", "partial")


class FrozenFeatureSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    set_key: str = Field(min_length=1, description="e.g. top_10 or all_features")
    feature_set_id: UUID
    feature_count: int = Field(ge=1)
    subject_count: int = Field(ge=1)


class ExtractedFeatures(BaseModel):
    """The final answer, checked by the engine before the agent may finish."""

    model_config = ConfigDict(extra="forbid")

    feature_classes: list[str] = Field(min_length=1)
    radiomics_rows: int = Field(ge=1, description="radiomics_rows.total of the extraction")
    feature_sets: list[FrozenFeatureSet] = Field(min_length=1)
    selection_method: str = Field(min_length=1)
    top_features: list[str] = Field(default_factory=list)
    label_supervised_on_full_data: bool
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_sets(self) -> ExtractedFeatures:
        keys = [item.set_key for item in self.feature_sets]
        if len(keys) != len(set(keys)):
            raise ValueError(f"a set_key is reported more than once: {keys}")
        return self


output_contract = ExtractedFeatures


async def completion_check(ctx: ToolContext, output: ExtractedFeatures | None) -> list[str]:
    """What is missing or wrong before the agent may finish. The ids and
    counts come from the model, so each is held against the recorded set; an
    unreachable Application API propagates and fails the attempt."""
    if not ctx.run_id:
        return ["this agent is not running inside a node; there is no run to record on"]
    if output is None:
        return ["finish with the frozen feature sets in the required structured format"]

    run_id = str(ctx.run_id)
    recorded = {
        str(item["feature_set_id"]): item
        for item in await domain_api.list_feature_sets(str(ctx.session_id))
        if str(item.get("created_by_run_id")) == run_id and item.get("frozen_at")
    }
    issues = []
    for reported in output.feature_sets:
        item = recorded.get(str(reported.feature_set_id))
        if item is None:
            issues.append(
                f"feature set {reported.feature_set_id} ({reported.set_key}) is not a frozen set "
                "of this run: report the ids radiomics_analyze_importance returned"
            )
            continue
        actual = (item.get("set_key"), item.get("feature_count"), item.get("subject_count"))
        claimed = (reported.set_key, reported.feature_count, reported.subject_count)
        if actual != claimed:
            issues.append(
                f"feature set {reported.feature_set_id} is recorded as set_key={actual[0]}, "
                f"{actual[1]} features, {actual[2]} subjects: report those values"
            )
    missing = sorted(
        item.get("set_key", key)
        for key, item in recorded.items()
        if key not in {str(reported.feature_set_id) for reported in output.feature_sets}
    )
    if missing:
        issues.append(f"report every frozen set of this run; missing: {missing}")
    if not recorded:
        issues.append(
            "this run froze no feature set: call radiomics_analyze_importance after the extraction"
        )

    run = await domain_api.get_run(run_id)
    agent_output = (run.get("scratchpad_summary") or {}).get("agent_output") or {}
    if agent_output.get("status") not in AGENT_OUTPUT_STATUSES:
        issues.append(
            "your structured output is not recorded: call write_agent_output with "
            "agent_id='feature_extraction_agent' and status 'completed'"
        )
    return issues


def extract_features(result: Any) -> list[ExtractedArtifact]:
    """One ``FeatureTable`` per frozen set, from the checked output."""
    output = ExtractedFeatures.model_validate(result.output)
    return [
        ExtractedArtifact(
            ref={"feature_set_id": str(item.feature_set_id)},
            summary={"n_rows": item.subject_count, "n_features": item.feature_count},
        )
        for item in output.feature_sets
    ]


def extract_importance(result: Any) -> ExtractedArtifact | None:
    """The ranking over ``all_features``; absent when that set was not frozen."""
    output = ExtractedFeatures.model_validate(result.output)
    table = next((item for item in output.feature_sets if item.set_key == ALL_FEATURES), None)
    if table is None:
        return None
    return ExtractedArtifact(
        ref={"feature_set_id": str(table.feature_set_id), "method": output.selection_method},
        summary={"top_features": output.top_features},
    )
