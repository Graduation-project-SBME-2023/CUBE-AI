"""Interim manifest presets for the moved tools (merged plan, Phase 2.1).

``ToolSpec`` requires ``side_effects``, ``cost``, ``execution`` and
``visibility``, so a tool cannot leave ``@mcp.tool`` without them. Phase 2.1
gives every tool only these, plus the ``read_only`` / ``destructive`` /
``idempotent`` flags its MCP ``annotations`` already declared. The rest of the
manifest (``resources``, ``data_scopes``, ``timeout_s``, ports, ``on_result``)
comes in Phase 2.4 for the light tools and in Plan 7 for the heavy ones.

Visibility is passed per tool, from the Phase 2.4 table. ``"graph"`` needs
output ports, so no tool gets it before Phase 2.4.
"""

from __future__ import annotations

from typing import Any

# Reads nothing but returns data: side_effects="none" requires read_only=True.
READ_ONLY: dict[str, Any] = {
    "side_effects": "none",
    "cost": "light",
    "execution": "inline",
    "read_only": True,
}

# Writes a domain row, a scratchpad entry or a workspace file.
WRITES: dict[str, Any] = {
    "side_effects": "writes",
    "cost": "light",
    "execution": "inline",
}

# PLACEHOLDER until Plan 7, which makes these tools cost="heavy",
# execution="job" with resources. Kept inline here so the catalog validates;
# grep for INTERIM_HEAVY to find every tool still waiting for that move.
INTERIM_HEAVY: dict[str, Any] = {
    "side_effects": "compute",
    "cost": "medium",
    "execution": "inline",
}
