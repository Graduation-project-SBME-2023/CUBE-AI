"""Hooks of ``main_agent`` (see ``toolkit/agents/loader.py``).

- ``allowed_tools_fn``: every tool with ``main`` visibility, which is what the
  Main may *call*. What it may *use as a node* is the compact catalog index
  added to its system prompt (``catalog.index("main")``, Phase 12.3). It
  chooses by visibility and never lists tool names, so the catalog validates
  before the control tools of Phase 12.2 exist.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def allowed_tools_fn(ctx: Any, tools: Sequence[Any]) -> list[str]:
    """Every ``main`` tool; the registry offers the Main nothing else."""
    return [tool.name for tool in tools]
