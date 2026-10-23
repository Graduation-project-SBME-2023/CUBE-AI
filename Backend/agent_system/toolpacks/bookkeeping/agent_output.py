"""The agent output tool: an agent's structured output, stored on its run.

``write_agent_output`` records this run's output (``PUT /runs/{run}/output``).
An agent reads the upstream results from its Node inputs, so there is no read
tool (Phase 0.2). The only files this module touches are the run's disposable
scratch directories under ``temp/``, removed once the output is recorded.
``write_agent_output`` itself goes in Phase 10.1, when the Node Runner records
the SDK's structured output.
"""

import asyncio
import json
import logging
import shutil
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field

from .._toolkit import error_result, tool
from ..common import domain_api, views
from ..common.manifests import WRITES
from storage_paths import TEMP_DIR

logger = logging.getLogger(__name__)

# TEMP_DIR/<category>/<run_id>/ is where every scratch-heavy tool keeps its
# working files (the PyRadiomics parameter file, modeling plots, ...). None of
# it is durable — the measurements and metrics are rows, the plots are
# published as artifacts — so once this run's output is recorded there is
# nothing left to keep it for. A tool that follows the same convention is
# covered without any cleanup code of its own.
_RUN_SCOPED_TEMP_CATEGORIES = ("feature-extraction", "feature-selection", "modeling")

OutputStatus = Literal["completed", "failed", "partial"]


def _cleanup_run_temp_dirs(run_id: str) -> None:
    for category in _RUN_SCOPED_TEMP_CATEGORIES:
        run_dir = TEMP_DIR / category / run_id
        if run_dir.exists():
            try:
                shutil.rmtree(run_dir)
            except Exception as exc:
                logger.warning("Failed to clean up temp dir %s: %s", run_dir, exc)


# -- Input models (unchanged shapes) -------------------------------------------

class WriteAgentOutputInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    agent_id: str = Field(
        ...,
        description="Unique identifier for this agent (e.g., 'population_planner', 'feature_extractor').",
        min_length=1, max_length=64,
    )
    agent_role: str = Field(
        ...,
        description="The agent's role in the pipeline (e.g., 'population_selector', 'feature_extraction').",
        min_length=1, max_length=128,
    )
    status: OutputStatus = Field(
        ...,
        description="Agent completion status: 'completed', 'failed', or 'partial'.",
    )
    inputs: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Dictionary of inputs the agent received. For example: "
            "{'hypothesis': '...', 'data_source': '...', 'upstream_agent': '...'}."
        ),
    )
    output_files: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Dictionary mapping logical file names to their paths (can be null if generation failed). For example: "
            "{'targets_csv': 'targets.csv', 'radiomics_features': null}."
        ),
    )
    output_metrics: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Dictionary of key metrics produced by the agent. For example: "
            "{'accuracy': 0.92, 'auc': 0.88, 'num_features': 150, 'cohort_size': 45}."
        ),
    )
    output_summary: str = Field(  # tool argument name, kept (plan.md global rules)
        default="",
        description="A concise text summary of what the agent accomplished and its key findings.",
        max_length=20_000,
    )
    errors: List[str] = Field(
        default_factory=list,
        description="List of error messages encountered during execution.",
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="List of warning messages or caveats about the outputs.",
    )


# -- Tools ---------------------------------------------------------------------

@tool(
    name="write_agent_output",
    visibility={"agent"},
    idempotent=True,
    data_scopes={"db_write", "fs_write"},
    timeout_s=60,
    **WRITES,
    on_result=views.on_agent_output,
)
async def write_agent_output(params: WriteAgentOutputInput) -> str | dict[str, Any]:
    """Write this agent's structured output."""
    run_id = domain_api.run_id()
    if not run_id:
        return error_result(
            "no_run",
            "This call belongs to no run; only an agent running inside a node can record its output.",
        )
    try:
        run = await domain_api.record_run_output({
            "agent_role": params.agent_role,
            "status": params.status,
            "inputs": params.inputs,
            "output_files": params.output_files,
            "output_metrics": params.output_metrics,
            "summary": params.output_summary,  # tool argument name, kept
            "errors": params.errors,
            "warnings": params.warnings,
        })
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc, "the output was not recorded")

    # The output is durable now. rmtree of a large scratch tree would block the
    # event loop (L12); H13 (keep the dirs while the run's jobs are active)
    # comes with the job platform (Plan 5).
    await asyncio.to_thread(_cleanup_run_temp_dirs, run_id)

    return json.dumps(
        {
            "status": "ok",
            "message": f"Output saved for agent '{params.agent_id}'.",
            "agent_run_id": run.get("run_id"),  # tool result key, kept (plan.md: no success result shape changes)
            "step_id": run.get("node_id"),  # tool result key, kept (plan.md: no success result shape changes)
        },
        indent=2,
    )
