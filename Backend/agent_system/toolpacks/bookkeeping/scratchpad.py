"""Multi-Agent Scratchpad MCP Tool."""

import json
from typing import Any, Dict, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .._toolkit import tool
from ..common.manifests import READ_ONLY, WRITES
from ..common.domain_api import agent_type, node_id, run_id, session_id
from ..common.emit import emit, emit_view
from ..common.http_client import shared_client, timeout, write_path_headers
from ..common.service_urls import app_api_url

AgentStatus = Literal["in_progress", "done", "blocked", "failed"]
FlagLevel = Literal["info", "warning", "error", "critical"]


REQUEST_TIMEOUT = 20.0


def _error(message: str) -> str:
    return json.dumps({"status": "error", "message": message}, indent=2)


async def _post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Write to this run's scratchpad. A failed write is an error, not a warning: there is no file to fall back to, and silently losing an agent's own record of its work is what current removes."""
    run = run_id()
    if not run:
        raise RuntimeError(
            "This call belongs to no run; only an agent running inside a node "
            "can write its scratchpad."
        )
    response = await shared_client().post(
        f"{app_api_url()}/runs/{run}/scratchpad/{path}",
        json=payload,
        headers=write_path_headers(),
        timeout=timeout(REQUEST_TIMEOUT),
    )
    if response.status_code >= 500:
        # A 5xx body is an unhandled server exception (stack frames, raw
        # SQL) -- never something to hand back to an agent that may quote
        # it verbatim in a generated report.
        raise RuntimeError(
            f"{response.status_code} from the Application API: an internal "
            "error occurred and the request could not complete."
        )
    if response.status_code >= 400:
        raise RuntimeError(
            f"{response.status_code} from the Application API: {response.text[:400]}"
        )
    return response.json()


async def _get(url: str) -> Dict[str, Any]:
    response = await shared_client().get(url, timeout=timeout(REQUEST_TIMEOUT))
    response.raise_for_status()
    return response.json()


def _notes(run: Dict[str, Any]) -> Dict[str, Any]:
    return run.get("scratchpad_notes") or {}


def _flags(run: Dict[str, Any]) -> list:
    return (run.get("scratchpad_warnings") or {}).get("items", [])


def _summary(run: Dict[str, Any]) -> Dict[str, Any] | None:
    return (run.get("scratchpad_summary") or {}).get("scratchpad_summary")


# -- Input models (unchanged shapes) -------------------------------------------

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

    level: FlagLevel = Field(..., description="Severity of the flag.")
    message: str = Field(
        ...,
        description="Clear description of the issue or observation for the reporting agent.",
        min_length=1, max_length=2_000,
    )


class SetStatusInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    status: AgentStatus = Field(..., description="Agent's current status.")


class ReadAgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_agent_id: str = Field(..., description="ID of the agent whose section to read.", min_length=1, max_length=64)
    include_notes: bool = Field(
        default=True,
        description="Whether to include the agent's private working notes.",
    )


# -- Tools ---------------------------------------------------------------------

@tool(
    name="scratchpad_register_agent",
    visibility={"harness"},
    data_scopes={"db_write"},
    timeout_s=60,
    **WRITES,
)
async def scratchpad_register_agent(role: str = "agent") -> str:
    """Register an agent in the scratchpad at the start of its work."""
    try:
        run = await _post("register", {"role": role})
    except Exception as exc:
        return _error(str(exc))

    emit("tool_log", f"Registering agent {agent_type()}.")

    await emit_view(
        "scratchpad_update",
        {
            "type": "agent_registered",
            "agent_id": agent_type(),
            "run_id": run_id(),
            "node_id": node_id(),
            "role": role,
            "status": "in_progress",
            "timestamp": _notes(run).get("registered_at"),
            "content": f"Agent {agent_type()} registered as {role}",
        },
    )

    return json.dumps(
        {
            "status": "ok",
            "agent_id": agent_type(),
            "agent_run_id": run_id(),  # tool result key, kept (plan.md: no success result shape changes)
            "role": role,
        },
        indent=2,
    )


@tool(
    name="scratchpad_write_note",
    visibility={"agent"},
    data_scopes={"db_write"},
    timeout_s=60,
    **WRITES,
)
async def scratchpad_write_note(content: str) -> str:
    """Append a private working note to this run's scratchpad section."""
    try:
        run = await _post("notes", {"content": content})
    except Exception as exc:
        return _error(str(exc))

    notes = _notes(run).get("notes", [])
    await emit_view(
        "scratchpad_update",
        {
            "type": "agent_note",
            "agent_id": agent_type(),
            "run_id": run_id(),
            "role": _notes(run).get("role", ""),
            "content": content,
            "timestamp": notes[-1]["created_at"] if notes else None,
            "note_index": len(notes) - 1,
        },
    )
    return json.dumps({"status": "ok", "note_index": len(notes) - 1}, indent=2)


@tool(
    name="scratchpad_raise_flag",
    visibility={"agent"},
    data_scopes={"db_write"},
    timeout_s=60,
    **WRITES,
)
async def scratchpad_raise_flag(level: str = "warning", message: str = "No message provided.") -> str:
    """Raise a flag so the reporting agent sees an issue."""
    if level not in ("info", "warning", "error", "critical"):
        return _error(
            f"Invalid level '{level}'. Must be one of: info, warning, error, critical."
        )
    try:
        run = await _post("flags", {"level": level, "message": message})
    except Exception as exc:
        return _error(str(exc))

    flags = _flags(run)
    await emit_view(
        "scratchpad_update",
        {
            "type": "agent_flag",
            "agent_id": agent_type(),
            "run_id": run_id(),
            "role": _notes(run).get("role", ""),
            "level": level,
            "content": message,
            "timestamp": flags[-1]["created_at"] if flags else None,
            "flag_index": len(flags) - 1,
        },
    )
    return json.dumps({"status": "ok", "flag_index": len(flags) - 1}, indent=2)


@tool(
    name="scratchpad_set_status",
    visibility={"harness"},
    idempotent=True,
    data_scopes={"db_write"},
    timeout_s=60,
    **WRITES,
)
async def scratchpad_set_status(status: str) -> str:
    """Update this run's self-reported working status."""
    if status not in ("in_progress", "done", "blocked", "failed"):
        return _error(
            f"Invalid status '{status}'. Must be one of: in_progress, done, blocked, failed."
        )
    try:
        current = await _get(f"{app_api_url()}/runs/{run_id()}") if run_id() else {}
        previous = _notes(current).get("status")
        run = await _post("status", {"status": status})
    except Exception as exc:
        return _error(str(exc))

    activity_log = _notes(run).get("activity_log") or []
    await emit_view(
        "scratchpad_update",
        {
            "type": "agent_status",
            "agent_id": agent_type(),
            "run_id": run_id(),
            "role": _notes(run).get("role", ""),
            "status": status,
            "previous_status": previous,
            "content": f"Status changed: {previous} -> {status}",
            "timestamp": activity_log[-1]["timestamp"] if activity_log else None,
        },
    )
    return json.dumps(
        {"status": "ok", "previous_status": previous, "new_status": status}, indent=2
    )


@tool(
    name="scratchpad_write_summary",
    visibility={"agent"},
    idempotent=True,
    data_scopes={"db_write"},
    timeout_s=60,
    **WRITES,
)
async def scratchpad_write_summary(summary: str, overwrite: bool = False) -> str:
    """Write this run's final work summary."""
    try:
        run = await _post("summary", {"summary": summary, "overwrite": overwrite})
    except Exception as exc:
        return _error(str(exc))

    await emit_view(
        "scratchpad_update",
        {
            "type": "agent_summary",
            "agent_id": agent_type(),
            "run_id": run_id(),
            "role": _notes(run).get("role", ""),
            "content": summary,
            "timestamp": (_summary(run) or {}).get("updated_at"),
            "summary_length": len(summary),
        },
    )

    emit("tool_log", "Writing a Summary.")

    return json.dumps({"status": "ok", "message": "Summary saved."}, indent=2)


@tool(
    name="scratchpad_read_agent",
    visibility={"agent"},
    idempotent=True,
    timeout_s=45,
    **READ_ONLY,
)
async def scratchpad_read_agent(target_agent_id: str, include_notes: bool = True) -> str:
    """Read the scratchpad section of an agent in this session.: several plan steps may name the same agent. This resolves the FIRST such step in plan order and reports how many matched, rather than pretending the agent name is a unique key."""
    if not session_id():
        return _error("This call belongs to no session.")
    try:
        resolved = await _get(
            f"{app_api_url()}/runs/session/{session_id()}/by-target/{target_agent_id}"
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return _error(f"Agent '{target_agent_id}' not found in this session.")
        return _error(f"Could not read agent '{target_agent_id}': {exc}")
    except Exception as exc:
        return _error(f"Could not read agent '{target_agent_id}': {exc}")

    run = resolved.get("run") or {}
    notes = _notes(run)
    section: Dict[str, Any] = {
        "agent_id": target_agent_id,
        "agent_run_id": run.get("run_id"),  # tool result key, kept (plan.md: no success result shape changes)
        "step_id": run.get("node_id"),  # tool result key, kept (plan.md: no success result shape changes)
        "steps_sharing_this_agent_type": resolved.get("nodes_sharing_this_target"),
        "role": notes.get("role", ""),
        "status": notes.get("status"),
        "run_status": run.get("status"),
        "registered_at": notes.get("registered_at"),
        "flags": _flags(run),
        "summary": _summary(run),
    }
    if include_notes:
        section["notes"] = notes.get("notes", [])

    emit("tool_log", f"Reading {target_agent_id}'s notes.")

    return json.dumps(section, indent=2)


@tool(
    name="scratchpad_read_report",
    visibility={"agent"},
    idempotent=True,
    timeout_s=45,
    **READ_ONLY,
)
async def scratchpad_read_report() -> str:
    """Read the reporter-focused view of this session's scratchpad."""
    if not session_id():
        return _error("This call belongs to no session.")
    try:
        report = await _get(f"{app_api_url()}/runs/session/{session_id()}/scratchpad")
    except Exception as exc:
        return _error(f"Could not read the scratchpad report: {exc}")
    return json.dumps(report, indent=2)

