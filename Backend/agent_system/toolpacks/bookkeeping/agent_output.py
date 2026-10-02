"""Agent Output MCP Tool."""

import json
import logging
import os
import shutil
from typing import Any, Dict, List, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from . import mcp
from storage_paths import TEMP_DIR

logger = logging.getLogger(__name__)

# TEMP_DIR/<category>/<run_id>/ is the convention every scratch-heavy
# tool (PyRadiomics params/log, PyCaret train.csv/log/plots, ...) writes
# its working files under — see feature_extraction/features.py and
# modeling/classification.py|regression.py. None of it is durable (the
# measurements/metrics land in Postgres, plots are re-published as
# artifacts), so once this run's output is confirmed saved there is nothing
# left to keep it for. A tool that follows the same <category>/<run_id>
# convention is covered automatically — no per-tool cleanup code needed.
_RUN_SCOPED_TEMP_CATEGORIES = ("feature-extraction", "feature-selection", "modeling")


def _cleanup_run_temp_dirs(run_id: str) -> None:
    for category in _RUN_SCOPED_TEMP_CATEGORIES:
        run_dir = TEMP_DIR / category / run_id
        if run_dir.exists():
            try:
                shutil.rmtree(run_dir)
            except Exception as exc:
                logger.warning("Failed to clean up temp dir %s: %s", run_dir, exc)

def _app_api_url() -> str:
    """The Application API base URL."""
    try:
        from service_urls import APP_API_URL

        return APP_API_URL
    except ImportError:
        return os.environ.get(
            "APP_API_URL", os.environ.get("CONTROL_API_URL", "http://localhost:8001")
        ).rstrip("/")


APP_API_URL = _app_api_url()
SESSION_ID = os.environ.get("SESSION_ID", "")
AGENT_ID = os.environ.get("AGENT_ID", "")
RUN_ID = os.environ.get("RUN_ID", "")
REQUEST_TIMEOUT = 30.0

OutputStatus = Literal["completed", "failed", "partial"]


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


class ReadAgentOutputInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(
        ...,
        description="ID of the agent whose output to read.",
        min_length=1, max_length=64,
    )


# -- Helpers -------------------------------------------------------------------

def _error(message: str) -> str:
    return json.dumps({"status": "error", "message": message}, indent=2)


def _document(run: Dict[str, Any]) -> Dict[str, Any]:
    """Present a run's stored output in the shape the file had."""
    node = run.get("node") or {}
    agent_output = (run.get("scratchpad_summary") or {}).get("agent_output") or {}
    warnings = [
        item.get("message")
        for item in (run.get("scratchpad_warnings") or {}).get("items", [])
        if item.get("message")
    ]
    return {
        "agent_id": node.get("target_name"),
        "agent_run_id": run.get("run_id"),  # tool result key, kept (plan.md: no success result shape changes)
        "step_id": run.get("node_id"),  # tool result key, kept (plan.md: no success result shape changes)
        "step_order": node.get("position"),  # tool result key, kept (plan.md: no success result shape changes)
        "agent_role": agent_output.get("agent_role", ""),
        "status": agent_output.get("status", run.get("status")),
        "timestamp": agent_output.get("recorded_at"),
        "inputs": agent_output.get("inputs") or {},
        "outputs": agent_output.get("outputs") or {},
        "errors": agent_output.get("errors") or [],
        "warnings": warnings,
    }


async def _session_runs() -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.get(f"{APP_API_URL}/runs/session/{SESSION_ID}")
        response.raise_for_status()
        return response.json()


# -- Tools ---------------------------------------------------------------------

@mcp.tool(
    name="write_agent_output",
    annotations={
        "title": "Write Structured Agent Output",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def write_agent_output(params: WriteAgentOutputInput) -> str:
    """Write this agent's structured output."""
    if not RUN_ID:
        return _error(
            "RUN_ID is not set in the MCP environment; this agent is not "
            "bound to an approved plan step and cannot record its output."
        )
    payload = {
        "agent_role": params.agent_role,
        "status": params.status,
        "inputs": params.inputs,
        "output_files": params.output_files,
        "output_metrics": params.output_metrics,
        "summary": params.output_summary,  # tool argument name, kept
        "errors": params.errors,
        "warnings": params.warnings,
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.put(
                f"{APP_API_URL}/runs/{RUN_ID}/output", json=payload
            )
            if response.status_code >= 500:
                # A 5xx body is an unhandled server exception (stack frames,
                # raw SQL) -- never something to hand back to an agent that
                # may quote it verbatim in a generated report.
                return _error(
                    f"{response.status_code} from the Application API: an internal "
                    "error occurred and the request could not complete."
                )
            if response.status_code >= 400:
                return _error(
                    f"{response.status_code} from the Application API: "
                    f"{response.text[:400]}"
                )
            run = response.json()
    except Exception as exc:
        return _error(f"Failed to write output: {exc}")

    # This agent's output is now durable in Postgres — its working scratch
    # under temp/ (if any) has nothing left it's the only copy of.
    _cleanup_run_temp_dirs(RUN_ID)

    return json.dumps(
        {
            "status": "ok",
            "message": f"Output saved for agent '{params.agent_id}'.",
            "agent_run_id": run.get("run_id"),  # tool result key, kept (plan.md: no success result shape changes)
            "step_id": run.get("node_id"),  # tool result key, kept (plan.md: no success result shape changes)
        },
        indent=2,
    )


@mcp.tool(
    name="read_agent_output",
    annotations={
        "title": "Read a Single Agent's Output",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def read_agent_output(params: ReadAgentOutputInput) -> str:
    """Read the structured output of an agent in this session.: when several plan steps name one agent, this resolves the FIRST in plan order and says how many matched."""
    if not SESSION_ID:
        return _error("SESSION_ID is not set in the MCP environment.")
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(
                f"{APP_API_URL}/runs/session/{SESSION_ID}"
                f"/by-target/{params.agent_id}"
            )
            if response.status_code == 404:
                return _error(f"No output found for agent '{params.agent_id}'.")
            response.raise_for_status()
            resolved = response.json()
    except Exception as exc:
        return _error(f"Failed to read output: {exc}")

    document = _document(resolved.get("run") or {})
    document["steps_sharing_this_agent_type"] = resolved.get("nodes_sharing_this_target")
    return json.dumps(document, indent=2)


@mcp.tool(
    name="read_all_agent_outputs",
    annotations={
        "title": "Read All Agent Outputs",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def read_all_agent_outputs() -> str:
    """Read every agent's structured output for this session, in plan order."""
    if not SESSION_ID:
        return _error("SESSION_ID is not set in the MCP environment.")
    try:
        runs = await _session_runs()
    except Exception as exc:
        return _error(f"Failed to read outputs: {exc}")

    agents: Dict[str, Any] = {}
    statuses = set()
    for run in runs:
        document = _document(run)
        if not document.get("timestamp"):
            continue  # this step has not written an output yet
        key = document["agent_id"] or str(document["step_id"])  # tool result key, kept (plan.md: no success result shape changes)
        if key in agents:
            # Two steps share this agent type — keep both, keyed by step.
            key = f"{key}#{document['step_order']}"  # tool result key, kept (plan.md: no success result shape changes)
        agents[key] = document
        statuses.add(document.get("status", "unknown"))

    if not agents:
        pipeline_status = "no_outputs"
    elif "failed" in statuses:
        pipeline_status = "has_failures"
    elif "partial" in statuses:
        pipeline_status = "has_partial"
    else:
        pipeline_status = "all_completed"

    return json.dumps(
        {
            "agent_count": len(agents),
            "agents": agents,
            "pipeline_status": pipeline_status,
        },
        indent=2,
    )
