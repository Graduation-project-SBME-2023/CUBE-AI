"""The frontend view that shows an agent node's result.

When an agent node completes, the node runner sends one view event built from
the agent's structured output. The payload has the shape the result views
already read (``agent_id``, ``run_id``, ``node_id``, ``position``, ``status``,
``timestamp``, ``inputs``, ``outputs``, ``errors``, ``warnings``), so the
frontend does not change. An agent with no view here sends nothing; the
modeling agent's output, for example, is read by the result reporter and has
no view of its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic_core import to_jsonable_python

from ..toolkit.events import ViewEvent
from .graph_store import Node
from .node_handlers import NodeInput

# The view event type of each agent, as the result views expect it.
AGENT_VIEWS: Mapping[str, str] = {
    "study_population_planner_agent": "population_output",
    "segmentation_agent": "segmentation_output",
    "feature_extraction_agent": "features_output",
    "result_reporter_agent": "report_output",
    "latex_reporter_agent": "latex_output",
}


def _as_dict(output: Any) -> dict[str, Any]:
    """The structured output as JSON-ready data; a non-mapping output is put under ``result``."""
    data = to_jsonable_python(output, fallback=str)
    return data if isinstance(data, dict) else {"result": data}


def agent_view(node: Node, run_id: str, output: Any, inputs: Mapping[str, NodeInput]) -> Optional[ViewEvent]:
    """Return the view event for a completed agent node, or ``None`` when its agent has no view.

    ``outputs`` is the structured output; ``warnings`` are the output's own
    ``warnings`` field when it has one. ``inputs`` lists each input port's
    artifact type, the upstream run it came from and its summary.
    """
    view_type = AGENT_VIEWS.get(node.target_name)
    if view_type is None:
        return None
    outputs = _as_dict(output)
    warnings = outputs.get("warnings")
    return ViewEvent(view_type, {
        "agent_id": node.target_name,
        "run_id": run_id,
        "node_id": node.node_id,
        "position": node.position,
        "agent_role": None,
        "status": "completed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            port: {"type": item.artifact_type, "run_id": item.run_id, "summary": to_jsonable_python(
                item.summary, fallback=str)}
            for port, item in inputs.items()
        },
        "outputs": outputs,
        "errors": [],
        "warnings": [str(w) for w in warnings] if isinstance(warnings, list) else [],
    })
