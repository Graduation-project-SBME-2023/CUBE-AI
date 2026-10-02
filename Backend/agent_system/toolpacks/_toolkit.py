"""The toolkit, imported under the same root as the tool packs.

``catalog.build()`` imports the packs under the toolkit's own root:
``agent_system.toolpacks`` when the toolkit was imported as
``agent_system.toolkit`` (the tests), bare ``toolpacks`` when ``agent_system``
is ``sys.path[0]`` (the container). ``spec_of()`` recognises a tool by its
``ToolSpec`` class, so a pack that imported the toolkit under the *other* root
would build its specs from a second copy of ``toolkit.tools.spec`` and its
tools would silently drop out of the catalog.

A relative ``from ..toolkit import ...`` cannot reach the toolkit when
``toolpacks`` is a top-level package, so the packs import it from here instead.
"""

from __future__ import annotations

if __name__.startswith("agent_system."):
    from agent_system.toolkit.context import NoToolContextError, ToolContext, current_context
    from agent_system.toolkit.events import (
        MarkdownPayload,
        ProgressPayload,
        TextPayload,
        ToolEvent,
        ViewEvent,
    )
    from agent_system.toolkit.tools.registry import tool
else:
    from toolkit.context import NoToolContextError, ToolContext, current_context
    from toolkit.events import MarkdownPayload, ProgressPayload, TextPayload, ToolEvent, ViewEvent
    from toolkit.tools.registry import tool

__all__ = [
    "MarkdownPayload",
    "NoToolContextError",
    "ProgressPayload",
    "TextPayload",
    "ToolContext",
    "ToolEvent",
    "ViewEvent",
    "current_context",
    "tool",
]
