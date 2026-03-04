"""
Agent Output MCP Tool

Provides structured JSON output writing for each agent in the pipeline.
Each agent writes its outputs to `workspace/outputs/{agent_id}_output.json`.
The reporter agent can read all outputs at once via `read_all_agent_outputs`.

Output JSON structure per agent:
{
  "agent_id": str,
  "agent_role": str,
  "status": "completed" | "failed" | "partial",
  "timestamp": ISO str,
  "inputs": { ... },
  "outputs": {
    "files": { "logical_name": "relative_path", ... },
    "metrics": { ... },
    "summary": str
  },
  "errors": [ str ],
  "warnings": [ str ]
}
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from . import mcp
from pydantic import BaseModel, ConfigDict, Field

# ── Constants ──────────────────────────────────────────────────────────────────

WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "../../agent_system/workspace")
OUTPUTS_DIR = os.path.join(WORKSPACE_DIR, "outputs")

OutputStatus = Literal["completed", "failed", "partial"]


# ── Input Models ───────────────────────────────────────────────────────────────

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
    output_files: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Dictionary mapping logical file names to their paths. For example: "
            "{'targets_csv': 'targets.csv', 'radiomics_features': 'features/radiomics_features.csv'}."
        ),
    )
    output_metrics: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Dictionary of key metrics produced by the agent. For example: "
            "{'accuracy': 0.92, 'auc': 0.88, 'num_features': 150, 'cohort_size': 45}."
        ),
    )
    output_summary: str = Field(
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


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_outputs_dir() -> str:
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    return OUTPUTS_DIR


def _output_path(agent_id: str) -> str:
    return os.path.join(OUTPUTS_DIR, f"{agent_id}_output.json")


# ── Tools ──────────────────────────────────────────────────────────────────────

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
    """Write the agent's structured output to a JSON file in the workspace.

    Each agent MUST call this tool when its work is complete. The output file
    is saved to `workspace/outputs/{agent_id}_output.json` and contains all
    key inputs, output file paths, metrics, and summary text.

    The reporter agent uses these structured outputs to build the final paper.

    Args:
        params (WriteAgentOutputInput):
            - agent_id: Unique agent identifier
            - agent_role: Agent's role description
            - status: 'completed' | 'failed' | 'partial'
            - inputs: Dict of inputs received
            - output_files: Dict of logical_name -> file_path
            - output_metrics: Dict of metric_name -> value
            - output_summary: Text summary of work done
            - errors: List of error messages
            - warnings: List of warnings/caveats

    Returns:
        str: JSON confirmation with the output file path.
    """
    try:
        _ensure_outputs_dir()

        output_data = {
            "agent_id": params.agent_id,
            "agent_role": params.agent_role,
            "status": params.status,
            "timestamp": _now(),
            "inputs": params.inputs,
            "outputs": {
                "files": params.output_files,
                "metrics": params.output_metrics,
                "summary": params.output_summary,
            },
            "errors": params.errors,
            "warnings": params.warnings,
        }

        file_path = _output_path(params.agent_id)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        return json.dumps({
            "status": "ok",
            "message": f"Output saved for agent '{params.agent_id}'.",
            "file_path": file_path,
        }, indent=2)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Failed to write output: {str(e)}",
        }, indent=2)


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
    """Read the structured output JSON file for a specific agent.

    Args:
        params (ReadAgentOutputInput):
            - agent_id: ID of the agent whose output to read.

    Returns:
        str: JSON content of the agent's output file, or error if not found.
    """
    file_path = _output_path(params.agent_id)
    if not os.path.exists(file_path):
        return json.dumps({
            "status": "error",
            "message": f"No output file found for agent '{params.agent_id}'.",
        }, indent=2)

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return json.dumps(data, indent=2)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Failed to read output: {str(e)}",
        }, indent=2)


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
    """Read all agent output JSON files from the workspace.

    Returns a combined view of every agent's structured output, including
    their inputs, output files, metrics, summaries, errors, and warnings.
    This is the primary data source for the reporter agent.

    Returns:
        str: JSON object with:
            - agent_count (int): Number of agent output files found
            - agents (dict): Keyed by agent_id, each containing the full output data
            - pipeline_status (str): 'all_completed' | 'has_failures' | 'has_partial' | 'no_outputs'
    """
    if not os.path.exists(OUTPUTS_DIR):
        return json.dumps({
            "agent_count": 0,
            "agents": {},
            "pipeline_status": "no_outputs",
        }, indent=2)

    agents = {}
    statuses = set()

    try:
        for filename in sorted(os.listdir(OUTPUTS_DIR)):
            if filename.endswith("_output.json"):
                file_path = os.path.join(OUTPUTS_DIR, filename)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    agent_id = data.get("agent_id", filename.replace("_output.json", ""))
                    agents[agent_id] = data
                    statuses.add(data.get("status", "unknown"))
                except Exception:
                    continue

        if not agents:
            pipeline_status = "no_outputs"
        elif "failed" in statuses:
            pipeline_status = "has_failures"
        elif "partial" in statuses:
            pipeline_status = "has_partial"
        else:
            pipeline_status = "all_completed"

        return json.dumps({
            "agent_count": len(agents),
            "agents": agents,
            "pipeline_status": pipeline_status,
        }, indent=2)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Failed to read outputs: {str(e)}",
        }, indent=2)
