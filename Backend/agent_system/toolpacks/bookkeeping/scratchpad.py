"""The scratchpad tools: an agent's notes, flags and summary.

They live on the agent's run (``node_runs.scratchpad_*``), keyed by the run of
the call's context — never by the agent's name, and never in a workspace file.
A write that does not land is an error: there is no file to fall back to.

Every change is also shown in the canvas as a ``scratchpad_update`` view.
A run's lifecycle (register, status) belongs to the Node Runner, and an agent
reads upstream results from its Node inputs, so the tools that did either are
gone (Phase 0.2).
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
from ..common.manifests import WRITES

FlagLevel = Literal["info", "warning", "error", "critical"]


def _text(max_length: int, description: str) -> Any:
    return Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length),
        Field(description=description),
    ]


NoteText = _text(10_000, "Private working note — intermediate thoughts, observations, partial results.")
FlagText = _text(2_000, "Clear description of the issue or observation for the reporting agent.")
SummaryText = _text(
    20_000,
    "Final summary of the agent's completed work. Should cover: what was done, key findings "
    "or outputs, and any caveats the reporting agent should know about.",
)


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
