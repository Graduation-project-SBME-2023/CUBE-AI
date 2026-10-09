"""``on_result`` hooks: a tool's result as frontend view updates (merged plan,
Phase 2.4 step 5).

Copied from ``services/pipeline_monitor.py`` (``_parse_tool_content``,
``_read_agent_output`` and the light-tool branches of
``_emit_tool_result_to_view``), which stays frozen. The legacy monitor picked
the view from the node's agent and the tool's name after the fact; here each
tool carries its hook, dispatch calls it after a success with the result and
the context, and the ``EventSink`` sends the returned ``ViewEvent``s. The view
types and payloads are the legacy ones, so the frontend views do not change.

The views of the heavy tools (radiomics, modeling) move with them in Plan 7.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from .. import _toolkit
from . import domain_api

POPULATION_AGENT = "study_population_planner_agent"


def parse_result(content: Any) -> dict[str, Any]:
    """A tool result as a dict, whatever form it took (``_parse_tool_content``)."""
    if isinstance(content, dict):
        return content
    try:
        parsed = json.loads(content) if isinstance(content, str) else content
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict) and "text" in parsed[0]:
            try:
                nested = json.loads(parsed[0]["text"])
            except json.JSONDecodeError:
                return {"raw": parsed[0]["text"][:2000]}
            return nested if isinstance(nested, dict) else {"data": nested}
        return {"data": parsed}
    except (json.JSONDecodeError, TypeError):
        return {"raw": str(content)[:2000]}


async def read_agent_output(session_id: str, agent_type: str) -> dict[str, Any]:
    """The structured output an agent recorded on its run, or ``{}``."""
    if not session_id or not agent_type:
        return {}
    for run in await domain_api.session_runs(session_id):
        node = run.get("node") or {}
        if node.get("target_name") != agent_type:
            continue
        agent_output = (run.get("scratchpad_summary") or {}).get("agent_output")
        if not agent_output:
            continue
        return {
            "agent_id": agent_type,
            "run_id": run.get("run_id"),
            "node_id": run.get("node_id"),
            # The legacy copy read `step`, a name Phase 6.1 retired, and raised
            # NameError here: the node is what carries the position now.
            "position": node.get("position"),
            "agent_role": agent_output.get("agent_role"),
            "status": agent_output.get("status"),
            "timestamp": agent_output.get("recorded_at"),
            "inputs": agent_output.get("inputs") or {},
            "outputs": agent_output.get("outputs") or {},
            "errors": agent_output.get("errors") or [],
            "warnings": [
                item.get("message")
                for item in (run.get("scratchpad_warnings") or {}).get("items", [])
            ],
        }
    return {}


def _branch(ctx: _toolkit.ToolContext) -> str | None:
    """The legacy monitor's branch for the node's agent: the first match, in
    its order (``_emit_tool_result_to_view``)."""
    agent = (ctx.agent_type or "").lower()
    if "population" in agent or "study_population" in agent:
        return "population"
    for marker in ("segmentation", "feature_extraction", "modeling", "result_reporter"):
        if marker in agent:
            return marker
    if "latex" in agent or "reporter" in agent:
        return "latex"
    return None


def _view(view_type: str, payload: dict[str, Any]) -> list[_toolkit.ViewEvent]:
    return [_toolkit.ViewEvent(view_type, payload)]


def _tool_view(view_type: str, tool_name: str, parsed: dict[str, Any], status: str | None = None):
    return _view(
        view_type,
        {"tool": tool_name, "status": status or parsed.get("status", "completed"), "data": parsed},
    )


# Per branch: the view write_agent_output refreshes and the agent whose
# recorded output it shows. The modeling summary is for the reporter, never a
# modeling view.
_OUTPUT_VIEWS: dict[str, tuple[str, str] | None] = {
    "population": ("population_output", POPULATION_AGENT),
    "segmentation": ("segmentation_output", "segmentation_agent"),
    "feature_extraction": ("features_output", "feature_extraction_agent"),
    "modeling": None,
    "result_reporter": ("report_output", "result_reporter_agent"),
    "latex": ("latex_output", "latex_reporter_agent"),
}


async def on_agent_output(result: Any, ctx: _toolkit.ToolContext) -> Sequence[_toolkit.ViewEvent]:
    """``write_agent_output``: the agent's view shows its recorded output."""
    target = _OUTPUT_VIEWS.get(_branch(ctx) or "")
    if target is None:
        return []
    view_type, agent_type = target
    recorded = await read_agent_output(str(ctx.session_id), agent_type)
    return _view(view_type, recorded or parse_result(result))


async def on_population_tool(result: Any, ctx: _toolkit.ToolContext) -> Sequence[_toolkit.ViewEvent]:
    """``generate_targets``, ``get_patient``, ``get_volume``: shown in the
    population view when the population agent called them."""
    if _branch(ctx) != "population":
        return []
    return _tool_view("population_output", str(ctx.tool_name), parse_result(result))


async def on_modeling_results(result: Any, ctx: _toolkit.ToolContext) -> Sequence[_toolkit.ViewEvent]:
    """``get_modeling_results``: shown in the report view for the result reporter."""
    if _branch(ctx) != "result_reporter":
        return []
    return _tool_view("report_output", str(ctx.tool_name), parse_result(result), status="completed")


async def on_latex_pdf(result: Any, ctx: _toolkit.ToolContext) -> Sequence[_toolkit.ViewEvent]:
    """``latex_to_pdf``: shown in the LaTeX view for the LaTeX reporter."""
    if _branch(ctx) != "latex":
        return []
    return _tool_view("latex_output", "latex_to_pdf", parse_result(result))
