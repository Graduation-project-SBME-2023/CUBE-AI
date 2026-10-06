"""The scratchpad tools: an agent's notes, flags, status and summary.

They live on the agent's run (``node_runs.scratchpad_*``), keyed by the run of
the call's context — never by the agent's name, and never in a workspace file.
A write that does not land is an error: there is no file to fall back to.

Every change is also shown in the canvas as a ``scratchpad_update`` view.
``write_note()`` and ``raise_flag()`` are what other packs call; they never
call the tools themselves.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from .._toolkit import error_result, tool
from ..common import domain_api
from ..common.emit import emit, emit_view
from ..common.manifests import READ_ONLY, WRITES

AgentStatus = Literal["in_progress", "done", "blocked", "failed"]
FlagLevel = Literal["info", "warning", "error", "critical"]


def _text(max_length: int, description: str) -> Any:
    return Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length),
        Field(description=description),
    ]


Role = _text(64, "The agent's role in the system (e.g. 'researcher', 'writer', 'validator').")
NoteText = _text(10_000, "Private working note — intermediate thoughts, observations, partial results.")
FlagText = _text(2_000, "Clear description of the issue or observation for the reporting agent.")
SummaryText = _text(
    20_000,
    "Final summary of the agent's completed work. Should cover: what was done, key findings "
    "or outputs, and any caveats the reporting agent should know about.",
)
AgentId = _text(64, "ID of the agent whose section to read.")


# ----------------------------------------------------------------------
# The run's scratchpad, through the Application API
# ----------------------------------------------------------------------
def _no_run() -> dict[str, Any]:
    return error_result(
        "no_run",
        "This call belongs to no run; only an agent running inside a node can write its scratchpad.",
    )


async def _write(section: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST one change to this run's scratchpad; the updated run."""
    run = domain_api.segment(domain_api.require_run())
    return await domain_api.request("POST", f"/runs/{run}/scratchpad/{section}", json=payload)


def _notes(run: dict[str, Any]) -> dict[str, Any]:
    return run.get("scratchpad_notes") or {}


def _flags(run: dict[str, Any]) -> list[dict[str, Any]]:
    return (run.get("scratchpad_warnings") or {}).get("items", [])


def _summary(run: dict[str, Any]) -> dict[str, Any] | None:
    return (run.get("scratchpad_summary") or {}).get("scratchpad_summary")


async def _show(event: str, run: dict[str, Any], **fields: Any) -> None:
    """The change, as the canvas shows it."""
    await emit_view(
        "scratchpad_update",
        {
            "type": event,
            "agent_id": domain_api.agent_type(),
            "run_id": domain_api.run_id(),
            "role": _notes(run).get("role", ""),
            **fields,
        },
    )


def _ok(**fields: Any) -> str:
    return json.dumps({"status": "ok", **fields}, indent=2)


def _not_written(exc: domain_api.DomainApiError) -> dict[str, Any]:
    return domain_api.error_result_from(exc, "the scratchpad was not updated")


# ----------------------------------------------------------------------
# For other packs
# ----------------------------------------------------------------------
async def write_note(content: str) -> str | dict[str, Any]:
    """Append a private working note to this run's scratchpad."""
    if not domain_api.run_id():
        return _no_run()
    try:
        run = await _write("notes", {"content": content})
    except domain_api.DomainApiError as exc:
        return _not_written(exc)
    notes = _notes(run).get("notes", [])
    await _show(
        "agent_note",
        run,
        content=content,
        timestamp=notes[-1]["created_at"] if notes else None,
        note_index=len(notes) - 1,
    )
    return _ok(note_index=len(notes) - 1)


async def raise_flag(level: FlagLevel, message: str) -> str | dict[str, Any]:
    """Raise a flag the reporting agent will see."""
    if not domain_api.run_id():
        return _no_run()
    try:
        run = await _write("flags", {"level": level, "message": message})
    except domain_api.DomainApiError as exc:
        return _not_written(exc)
    flags = _flags(run)
    await _show(
        "agent_flag",
        run,
        level=level,
        content=message,
        timestamp=flags[-1]["created_at"] if flags else None,
        flag_index=len(flags) - 1,
    )
    return _ok(flag_index=len(flags) - 1)


# ----------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------
@tool(
    name="scratchpad_register_agent",
    visibility={"harness"},
    data_scopes={"db_write"},
    timeout_s=60,
    **WRITES,
)
async def scratchpad_register_agent(role: Role = "agent") -> str | dict[str, Any]:
    """Register an agent in the scratchpad at the start of its work."""
    if not domain_api.run_id():
        return _no_run()
    try:
        run = await _write("register", {"role": role})
    except domain_api.DomainApiError as exc:
        return _not_written(exc)

    emit("tool_log", f"Registering agent {domain_api.agent_type()}.")
    await _show(
        "agent_registered",
        run,
        node_id=domain_api.node_id(),
        role=role,
        status="in_progress",
        timestamp=_notes(run).get("registered_at"),
        content=f"Agent {domain_api.agent_type()} registered as {role}",
    )
    return _ok(
        agent_id=domain_api.agent_type(),
        agent_run_id=domain_api.run_id(),  # tool result key, kept (plan.md: no success result shape changes)
        role=role,
    )


@tool(
    name="scratchpad_write_note",
    visibility={"agent"},
    data_scopes={"db_write"},
    timeout_s=60,
    **WRITES,
)
async def scratchpad_write_note(content: NoteText) -> str | dict[str, Any]:
    """Append a private working note to this run's scratchpad section."""
    return await write_note(content)


@tool(
    name="scratchpad_raise_flag",
    visibility={"agent"},
    data_scopes={"db_write"},
    timeout_s=60,
    **WRITES,
)
async def scratchpad_raise_flag(
    level: Annotated[FlagLevel, Field(description="Severity of the flag.")] = "warning",
    message: FlagText = "No message provided.",
) -> str | dict[str, Any]:
    """Raise a flag so the reporting agent sees an issue."""
    return await raise_flag(level, message)


@tool(
    name="scratchpad_set_status",
    visibility={"harness"},
    idempotent=True,
    data_scopes={"db_write"},
    timeout_s=60,
    **WRITES,
)
async def scratchpad_set_status(
    status: Annotated[AgentStatus, Field(description="Agent's current status.")],
) -> str | dict[str, Any]:
    """Update this run's self-reported working status."""
    if not domain_api.run_id():
        return _no_run()
    try:
        previous = _notes(await domain_api.get_run()).get("status")
        run = await _write("status", {"status": status})
    except domain_api.DomainApiError as exc:
        return _not_written(exc)

    activity_log = _notes(run).get("activity_log") or []
    await _show(
        "agent_status",
        run,
        status=status,
        previous_status=previous,
        content=f"Status changed: {previous} -> {status}",
        timestamp=activity_log[-1]["timestamp"] if activity_log else None,
    )
    return _ok(previous_status=previous, new_status=status)


@tool(
    name="scratchpad_write_summary",
    visibility={"agent"},
    idempotent=True,
    data_scopes={"db_write"},
    timeout_s=60,
    **WRITES,
)
async def scratchpad_write_summary(
    summary: SummaryText,
    overwrite: Annotated[
        bool,
        Field(description="Replace an existing summary. Defaults to False to prevent accidental overwrites."),
    ] = False,
) -> str | dict[str, Any]:
    """Write this run's final work summary."""
    if not domain_api.run_id():
        return _no_run()
    try:
        run = await _write("summary", {"summary": summary, "overwrite": overwrite})
    except domain_api.DomainApiError as exc:
        return _not_written(exc)

    await _show(
        "agent_summary",
        run,
        content=summary,
        timestamp=(_summary(run) or {}).get("updated_at"),
        summary_length=len(summary),
    )
    emit("tool_log", "Writing a Summary.")
    return _ok(message="Summary saved.")


@tool(
    name="scratchpad_read_agent",
    visibility={"agent"},
    idempotent=True,
    timeout_s=45,
    **READ_ONLY,
)
async def scratchpad_read_agent(
    target_agent_id: AgentId,
    include_notes: Annotated[
        bool, Field(description="Whether to include the agent's private working notes.")
    ] = True,
) -> str | dict[str, Any]:
    """Read the scratchpad section of an agent in this session. Several plan
    steps may name the same agent: this resolves the FIRST such step in plan
    order and reports how many matched, rather than pretending the agent name
    is a unique key."""
    if not domain_api.session_id():
        return error_result("no_session", "This call belongs to no session.")
    try:
        resolved = await domain_api.run_by_target(target_agent_id)
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc, f"Could not read agent '{target_agent_id}'")
    if resolved is None:
        return error_result("not_found", f"Agent '{target_agent_id}' not found in this session.")

    run = resolved.get("run") or {}
    notes = _notes(run)
    section: dict[str, Any] = {
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
async def scratchpad_read_report() -> str | dict[str, Any]:
    """Read the reporter-focused view of this session's scratchpad."""
    if not domain_api.session_id():
        return error_result("no_session", "This call belongs to no session.")
    try:
        report = await domain_api.request(
            "GET", f"/runs/session/{domain_api.segment(domain_api.session_id())}/scratchpad"
        )
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc, "Could not read the scratchpad report")
    return json.dumps(report, indent=2)
