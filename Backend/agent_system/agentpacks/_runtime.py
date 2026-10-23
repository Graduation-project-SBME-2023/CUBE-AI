"""The toolkit and the tool packs' helpers, imported under the agent packs' root.

``catalog.build()`` imports the agent packs under the toolkit's own root:
``agent_system.agentpacks`` when the toolkit was imported as
``agent_system.toolkit`` (the tests), bare ``agentpacks`` when
``agent_system`` is ``sys.path[0]`` (the container). A hook that imported the
toolkit under the *other* root would build its ``ExtractedArtifact`` (and
read its ``ToolContext``) from a second copy of the toolkit, which nothing
else would recognise. Hooks therefore import from here, relatively, the same
way the tool packs use ``toolpacks._toolkit``.

``segmentation_contracts`` and ``segmentation_policy`` are the segmentation
domain module's own output models and clinical target policy (plain pydantic
and text, no AutoGen): the segmentation internal agents use them as they are,
so their contract and policy have one source. ``literature_run_state`` is
the literature tools' per-run state, which the literature review's hooks own.
"""

from __future__ import annotations

if __name__.startswith("agent_system."):
    from agent_system.agents.execution_agents.segmentation import (
        clinical_target_policy as segmentation_policy,
        contracts as segmentation_contracts,
    )
    from agent_system.toolkit.context import ToolContext, current_context
    from agent_system.toolkit.events import ViewEvent
    from agent_system.toolkit.ports import ExtractedArtifact
    from agent_system.toolpacks.common import domain_api
    from agent_system.toolpacks.literature import run_state as literature_run_state
else:
    from agents.execution_agents.segmentation import (
        clinical_target_policy as segmentation_policy,
        contracts as segmentation_contracts,
    )
    from toolkit.context import ToolContext, current_context
    from toolkit.events import ViewEvent
    from toolkit.ports import ExtractedArtifact
    from toolpacks.common import domain_api
    from toolpacks.literature import run_state as literature_run_state

__all__ = [
    "ExtractedArtifact",
    "ToolContext",
    "ViewEvent",
    "current_context",
    "domain_api",
    "literature_run_state",
    "segmentation_contracts",
    "segmentation_policy",
]
