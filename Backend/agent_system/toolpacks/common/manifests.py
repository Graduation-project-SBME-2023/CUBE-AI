"""Manifest presets of the tool packs.

``ToolSpec`` requires ``side_effects``, ``cost``, ``execution`` and
``visibility``; these presets fix the first three for the common kinds of
tool. Every tool still states the rest itself: ``visibility``,
``data_scopes``, ``resources`` and ``timeout_s`` (dispatch enforces it; the
120 s default suits no heavy tool).
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

# PLACEHOLDER until the job platform (Plan 5), which makes these tools
# cost="heavy", execution="job". Kept inline so the catalog validates; grep
# for INTERIM_HEAVY to find every tool still waiting for that move. Each of
# them states its own timeout_s and resources.
INTERIM_HEAVY: dict[str, Any] = {
    "side_effects": "compute",
    "cost": "medium",
    "execution": "inline",
}
