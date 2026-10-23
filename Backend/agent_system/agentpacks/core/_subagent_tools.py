"""The tools of the Main's two subagents (Phase 3.4).

The Composer and the Recovery Agent read the graph and the catalog, and
propose a change with ``graph_propose``; neither may apply or execute
anything. Their allowlists are chosen at run time from the ``main`` tools that
exist, so the catalog validates before the control tools of Phase 12.2 do.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

READ_TOOLS = ("graph_inspect", "registry_search", "registry_describe", "artifact_read")
PROPOSE_TOOL = "graph_propose"
SUBAGENT_TOOLS = (*READ_TOOLS, PROPOSE_TOOL)


def subagent_tools(ctx: Any, tools: Sequence[Any]) -> list[str]:
    """``SUBAGENT_TOOLS`` that are registered, in that order."""
    available = {tool.name for tool in tools}
    return [name for name in SUBAGENT_TOOLS if name in available]
