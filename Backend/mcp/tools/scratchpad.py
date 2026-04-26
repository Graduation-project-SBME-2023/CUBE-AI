"""
Multi-Agent Scratchpad MCP Tool

A shared scratchpad for multi-agent systems. Each agent writes to its own
isolated section. A reporting agent can read a clean cross-agent view
(summaries + flags + activity sequence) without wading through raw notes.

scratchpad.json structure:
{
  "agents": {
    "<agent_id>": {
      "agent_id": str,
      "role": str,
      "status": "in_progress" | "done" | "blocked" | "failed",
      "registered_at": ISO str,
      "notes": [ { "content", "created_at" } ],
      "flags": [ { "level", "message", "created_at" } ],
      "summary": { "content", "updated_at" } | null
    }
  },
  "activity_log": [
    { "agent_id", "role", "action", "detail", "timestamp" }
  ]
}
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from . import mcp
from pydantic import BaseModel, ConfigDict, Field
from tool_logging import log_tool_state, ToolEvent, TextPayload

# ── Constants ──────────────────────────────────────────────────────────────────

WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "../../agent_system/workspace")
SCRATCHPAD_FILE = os.path.join("Backend/agent_system", WORKSPACE_DIR, "scratchpad.json")# SCRATCHPAD_FILE = os.path.join(WORKSPACE_DIR, "scratchpad.json")
AgentStatus = Literal["in_progress", "done", "blocked", "failed"]
FlagLevel = Literal["info", "warning", "error", "critical"]

current_agent_id = os.environ.get("AGENT_ID")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load() -> Dict[str, Any]:
    if not os.path.exists(SCRATCHPAD_FILE):
        return {"agents": {}, "activity_log": []}
    with open(SCRATCHPAD_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: Dict[str, Any]) -> None:
    # Ensure the parent directories exist before writing
    os.makedirs(os.path.dirname(SCRATCHPAD_FILE), exist_ok=True)
    
    with open(SCRATCHPAD_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_agent(data: Dict[str, Any], agent_id: str) -> Optional[Dict[str, Any]]:
    return data["agents"].get(agent_id)


def _require_agent(data: Dict[str, Any], agent_id: str) -> Dict[str, Any]:
    agent = _get_agent(data, agent_id)
    if agent is None:
        raise ValueError(
            f"Agent '{agent_id}' is not registered. Call scratchpad_register_agent first."
        )
    return agent


def _log_activity(data: Dict[str, Any], agent_id: str, role: str, action: str, detail: str) -> None:
    data["activity_log"].append({
        "agent_id": agent_id,
        "role": role,
        "action": action,
        "detail": detail,
        "timestamp": _now(),
    })


# ── Input Models ───────────────────────────────────────────────────────────────

class RegisterAgentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    role: str = Field(
        ...,
        description="The agent's role in the system (e.g. 'researcher', 'writer', 'validator').",
        min_length=1, max_length=64,
    )


class WriteNoteInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    content: str = Field(
        ...,
        description="Private working note — intermediate thoughts, observations, partial results.",
        min_length=1, max_length=10_000,
    )


class WriteSummaryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    summary: str = Field(
        ...,
        description=(
            "Final summary of the agent's completed work. Should cover: what was done, "
            "key findings or outputs, and any caveats the reporting agent should know about."
        ),
        min_length=1, max_length=20_000,
    )
    overwrite: bool = Field(
        default=False,
        description="Replace an existing summary. Defaults to False to prevent accidental overwrites.",
    )


class RaiseFlagInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    level: FlagLevel = Field(
        ...,
        description=(
            "Severity of the flag: "
            "'info' = noteworthy but not problematic, "
            "'warning' = potential issue, "
            "'error' = something went wrong, "
            "'critical' = blocking issue requiring immediate attention."
        ),
    )
    message: str = Field(
        ...,
        description="Clear description of the issue or observation for the reporting agent.",
        min_length=1, max_length=2_000,
    )


class SetStatusInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    status: AgentStatus = Field(
        ...,
        description=(
            "Agent's current status: "
            "'in_progress' = actively working, "
            "'done' = work complete and summary written, "
            "'blocked' = cannot proceed (raise a flag too), "
            "'failed' = encountered a fatal error."
        ),
    )


class ReadAgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_agent_id: str = Field(..., description="ID of the agent whose section to read.", min_length=1, max_length=64)
    include_notes: bool = Field(
        default=True,
        description="Whether to include the agent's private working notes.",
    )


# ── Tools ──────────────────────────────────────────────────────────────────────
@mcp.tool(
    name="scratchpad_register_agent",
    annotations={
        "title": "Register Agent in Scratchpad",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def scratchpad_register_agent(params: RegisterAgentInput) -> str:
    """Register an agent in the scratchpad at the start of its work.

    Must be called before any other scratchpad tool. Creates an isolated section
    for the agent keyed by agent_id, and logs the registration to the activity log.

    Args:
        params (RegisterAgentInput):
            - role (str): Agent's role (e.g. 'researcher', 'writer', 'ROI extractor', 'modeler').

    Returns:
        str: JSON confirmation, or error if agent_id is already registered.
    """
    data = _load()
    if current_agent_id in data["agents"]:
        return json.dumps({
            "status": "error",
            "message": f"Agent '{current_agent_id}' is already registered.",
        }, indent=2)

    data["agents"][current_agent_id] = {
        "agent_id": current_agent_id,
        "role": params.role,
        "status": "in_progress",
        "registered_at": _now(),
        "notes": [],
        "flags": [],
        "summary": None,
    }
    _log_activity(data, current_agent_id, params.role, "registered", f"Role: {params.role}")
    _save(data)

    # Calculate the absolute directory path
    import os
    save_directory = os.path.abspath(os.path.dirname(SCRATCHPAD_FILE))

    try:
        payload = TextPayload(message=f'Registering agent {current_agent_id}.')
        start_event = ToolEvent('tool_started', 'text', payload)
        log_tool_state(start_event)
        finished_event = ToolEvent('tool_finished', 'text', payload)
        log_tool_state(finished_event)
    except Exception as e:
        print(f"Warning: Could not connect to logging server: {e}")

    # Return the save_directory in the final JSON response
    return json.dumps({
        "status": "ok", 
        "agent_id": current_agent_id, 
        "role": params.role,
        "save_path": save_directory
    }, indent=2)

@mcp.tool(
    name="scratchpad_write_note",
    annotations={
        "title": "Write Private Working Note",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def scratchpad_write_note(params: WriteNoteInput) -> str:
    """Append a private working note to the agent's scratchpad section.

    Use this for intermediate thoughts, observations, or partial results during
    a task. Notes are only visible when explicitly reading a single agent's section
    and are excluded from the reporter's cross-agent view.

    Args:
        params (WriteNoteInput):
            - content (str): Note text.

    Returns:
        str: JSON confirmation with the note index.
    """
    data = _load()
    try:
        agent = _require_agent(data, current_agent_id)
    except ValueError as e:
        return json.dumps({"status": "error", "message": str(e)}, indent=2)

    note = {"content": params.content, "created_at": _now()}
    agent["notes"].append(note)
    _log_activity(data, current_agent_id, agent["role"], "wrote_note",
                  f"Note #{len(agent['notes']) - 1}")
    _save(data)
    return json.dumps({"status": "ok", "note_index": len(agent["notes"]) - 1}, indent=2)


@mcp.tool(
    name="scratchpad_raise_flag",
    annotations={
        "title": "Raise a Flag for the Reporting Agent",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def scratchpad_raise_flag(params: RaiseFlagInput) -> str:
    """Raise a flag in the agent's section to surface issues to the reporting agent.

    Use this to highlight anything the reporter should pay attention to:
    data quality issues, unexpected results, blockers, or errors.
    Flags appear prominently in the reporter's cross-agent view.

    Args:
        params (RaiseFlagInput):
            - level (str): 'info' | 'warning' | 'error' | 'critical'
            - message (str): Description of the issue.

    Returns:
        str: JSON confirmation with flag index.
    """
    data = _load()
    try:
        agent = _require_agent(data, current_agent_id)
    except ValueError as e:
        return json.dumps({"status": "error", "message": str(e)}, indent=2)

    flag = {"level": params.level, "message": params.message, "created_at": _now()}
    agent["flags"].append(flag)
    _log_activity(data, current_agent_id, agent["role"], "raised_flag",
                  f"[{params.level.upper()}] {params.message[:120]}")
    _save(data)
    return json.dumps({"status": "ok", "flag_index": len(agent["flags"]) - 1}, indent=2)


@mcp.tool(
    name="scratchpad_set_status",
    annotations={
        "title": "Set Agent Status",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def scratchpad_set_status(params: SetStatusInput) -> str:
    """Update the calling agent's status in the scratchpad.

    Call this whenever your state changes. The reporting agent uses statuses
    to know whether all agents have finished before starting its review.
    If setting status to 'blocked' or 'failed', also call scratchpad_raise_flag.

    Args:
        params (SetStatusInput):
            - status (str): 'in_progress' | 'done' | 'blocked' | 'failed'

    Returns:
        str: JSON confirmation with previous and new status.
    """
    data = _load()
    try:
        agent = _require_agent(data, current_agent_id)
    except ValueError as e:
        return json.dumps({"status": "error", "message": str(e)}, indent=2)

    previous = agent["status"]
    agent["status"] = params.status
    _log_activity(data, current_agent_id, agent["role"], "status_change",
                  f"{previous} → {params.status}")
    _save(data)
    return json.dumps({"status": "ok", "previous_status": previous, "new_status": params.status}, indent=2)


@mcp.tool(
    name="scratchpad_write_summary",
    annotations={
        "title": "Write Final Work Summary",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def scratchpad_write_summary(params: WriteSummaryInput) -> str:
    """Write the agent's final work summary. Call this when the task is complete.

    The summary is the primary input the reporting agent uses to review this
    agent's work. It should be self-contained and cover: what was done, key
    outputs or findings, and any caveats. After writing the summary, set your
    status to 'done' via scratchpad_set_status.

    Args:
        params (WriteSummaryInput):
            - summary (str): Full summary text.
            - overwrite (bool): Replace existing summary (default False).

    Returns:
        str: JSON confirmation, or error if summary exists and overwrite=False.
    """
    data = _load()
    try:
        agent = _require_agent(data, current_agent_id)
    except ValueError as e:
        return json.dumps({"status": "error", "message": str(e)}, indent=2)

    if agent["summary"] is not None and not params.overwrite:
        return json.dumps({
            "status": "error",
            "message": "A summary already exists for this agent. Set overwrite=true to replace it.",
        }, indent=2)

    agent["summary"] = {"content": params.summary, "updated_at": _now()}
    _log_activity(data, current_agent_id, agent["role"], "wrote_summary",
                  f"{len(params.summary)} chars")
    _save(data)

    payload = TextPayload(message=f'Writing a Summary.')
    start_event = ToolEvent('tool_started', 'text', payload)
    log_tool_state(start_event)
    finished_event = ToolEvent('tool_finished', 'text', payload)
    log_tool_state(finished_event)

    return json.dumps({"status": "ok", "message": "Summary saved."}, indent=2)


@mcp.tool(
    name="scratchpad_read_agent",
    annotations={
        "title": "Read a Single Agent's Scratchpad Section",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
@mcp.tool(
    name="scratchpad_read_agent",
    annotations={
        "title": "Read a Single Agent's Scratchpad Section",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def scratchpad_read_agent(params: ReadAgentInput) -> str:
    """Read the full scratchpad section for a specific agent.

    Returns the agent's status, flags, summary, and optionally their private notes.
    Any agent can read any other agent's section, enabling context sharing.

    Args:
        params (ReadAgentInput):
            - target_agent_id (str): ID of the agent to read.
            - include_notes (bool): Include private working notes (default True).

    Returns:
        str: JSON of the agent's full section, with notes optionally omitted.
    """
    data = _load()
    # FIX 1: Change params.agent_id to params.target_agent_id
    agent = _get_agent(data, params.target_agent_id)
    if agent is None:
        return json.dumps({
            "status": "error",
            # FIX 2: Change params.agent_id to params.target_agent_id
            "message": f"Agent '{params.target_agent_id}' not found.",
        }, indent=2)

    result = {k: v for k, v in agent.items() if k != "notes" or params.include_notes}

    # FIX 3: Change params.agent_id to params.target_agent_id
    try:
        payload = TextPayload(message=f"Reading {params.target_agent_id}'s notes.")
        start_event = ToolEvent('tool_started', 'text', payload)
        log_tool_state(start_event)
        finished_event = ToolEvent('tool_finished', 'text', payload)
        log_tool_state(finished_event)
    except Exception as e:
        print(f"Warning: Could not connect to logging server: {e}")

    return json.dumps(result, indent=2)


@mcp.tool(
    name="scratchpad_read_report",
    annotations={
        "title": "Read Full Cross-Agent Report View",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def scratchpad_read_report() -> str:
    """Read the reporter-focused view of the scratchpad.

    Returns a structured report containing:
    - overall_status: whether all agents are done, any are blocked/failed, or still in progress
    - agents: each agent's id, role, status, flags, and summary (no raw notes)
    - activity_log: full chronological sequence of all agent actions

    This is the primary tool for the reporting agent. It surfaces everything
    needed to audit agents' work without the noise of private working notes.

    Returns:
        str: JSON report view with fields:
            - overall_status (str): 'all_done' | 'in_progress' | 'blocked' | 'failed' | 'no_agents'
            - agent_count (int): Total number of registered agents.
            - agents (list): Per-agent summary with id, role, status, flags, summary.
            - activity_log (list): Chronological log of all agent actions.
    """
    data = _load()
    agents = data.get("agents", {})

    statuses = {a["status"] for a in agents.values()}
    if not agents:
        overall = "no_agents"
    elif "failed" in statuses:
        overall = "failed"
    elif "blocked" in statuses:
        overall = "blocked"
    elif statuses - {"done"}:
        overall = "in_progress"
    else:
        overall = "all_done"

    agent_report = []
    for agent in agents.values():
        agent_report.append({
            "agent_id": agent["agent_id"],
            "role": agent["role"],
            "status": agent["status"],
            "registered_at": agent["registered_at"],
            "flag_count": len(agent["flags"]),
            "flags": agent["flags"],
            "has_summary": agent["summary"] is not None,
            "summary": agent["summary"],
        })

    report = {
        "overall_status": overall,
        "agent_count": len(agents),
        "agents": agent_report,
        "activity_log": data.get("activity_log", []),
    }
    return json.dumps(report, indent=2)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
