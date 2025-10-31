# filepath: [__init__.py](http://_vscodecontentref_/0)
"""
agents package

Provides a tiny public API:
    * ``list_agents()`` – list all available agents with description.
    * ``get_agent(name)`` – retrieve the concrete class for a given name.
    * ``agents_description_for_prompt()`` – ready‑to‑paste string for LLM prompts.
"""

from mas.core.agent_registry import (
    AgentInfo,
    get_agent,
    list_agents,
    agents_description_for_prompt,
)

__all__ = [
    "AgentInfo",
    "get_agent",
    "list_agents",
    "agents_description_for_prompt",
]