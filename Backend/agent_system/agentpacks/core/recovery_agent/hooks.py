"""Hooks of ``recovery_agent`` (see ``toolkit/agents/loader.py``).

- ``allowed_tools_fn``: the same tools as the Composer: the read-only ``main``
  tools and ``graph_propose`` (``core/_subagent_tools.py``); never
  ``graph_apply`` or ``execute``.
"""

from __future__ import annotations

from .._subagent_tools import subagent_tools as allowed_tools_fn

__all__ = ["allowed_tools_fn"]
