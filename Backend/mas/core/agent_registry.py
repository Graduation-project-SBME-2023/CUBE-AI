"""
Agent Registry

Scans the ``mas.agents`` package, imports each ``*_agent.py`` module,
extracts its top‑level docstring (the human‑readable description you wrote
at the top of the file) and registers the concrete class.

The registry is used by:
* **Planner** – to list available modules in the “ModuleMatcher” prompt.
* **Executer** – to instantiate the correct class for a matched step.

Only agents that expose a *public class* whose name matches the file
(e.g. ``population_agent.py`` → ``PopulationSelector``) are registered.
"""

import importlib
import inspect
import logging
import pkgutil
import pathlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Type

import sys

# ----------------------------------------------------------------------
# Make sure the project root (the folder that contains ``mas``) is on sys.path
# ----------------------------------------------------------------------
ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]   # points to ``Backend`` folder
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# ----------------------------------------------------------------------
# Dataclass that holds everything the rest of the system needs about an agent
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class AgentInfo:
    """Container for a single agent."""
    name: str                     # Human‑readable name (e.g. "PopulationSelector")
    description: str              # Docstring extracted from the source file
    cls: Type                     # The concrete Python class implementing the agent
    module_path: str              # Fully‑qualified import path (e.g. "mas.agents.population_agent")

# ----------------------------------------------------------------------
# Internal cache – populated on first call to ``load_all_agents()``
# ----------------------------------------------------------------------
_AGENT_REGISTRY: Dict[str, AgentInfo] = {}

def _extract_docstring(file_path: pathlib.Path) -> str:
    """
    Reads ``file_path`` and returns the first top‑level triple‑quoted string.
    If no docstring is found, an empty string is returned.
    """
    source = file_path.read_text(encoding="utf-8")
    # Look for the first triple‑quoted string (""" … """ or ''' … ''')
    for quote in ('"""', "'''"):
        start = source.find(quote)
        if start != -1:
            end = source.find(quote, start + len(quote))
            if end != -1:
                return source[start + len(quote) : end].strip()
    return ""

def _load_agent_module(module_name: str) -> Optional[AgentInfo]:
    """
    Imports ``module_name`` (e.g. ``mas.agents.population_agent``) and
    returns an ``AgentInfo`` if the module defines a public class whose
    name matches the expected convention.

    Expected convention:
        * file name:   <name>_agent.py
        * class name: Capitalised <Name> (e.g. PopulationSelector)
    """
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # pragma: no cover – import failures are logged
        logger = logging.getLogger(__name__)
        logger.error("Failed to import agent module %s: %s", module_name, exc)
        return None

    # Derive the expected class name from the file name
    base_name = module_name.split(".")[-1]          # e.g. "population_agent"
    core = base_name.replace("_agent", "")          # e.g. "population"

    # Heuristic mapping for the few known agents
    heuristic_map = {
        "population": "PopulationSelector",
        "imaging": "ImagingProtocolDesigner",
        "segmentation": "SegmentationModule",
        "feature_extraction": "RadiomicsExtractor",
        "modeling": "ModelingModule",
        "validation": "ValidationModule",
        "outcome": "OutcomeAgent",
        "qa": "QAAgent",
        "reporting": "ReportingAgent",
    }
    expected_class_name = heuristic_map.get(core, core.title().replace("_", ""))

    # Find the class object inside the module
    cls = getattr(module, expected_class_name, None)
    if cls is None or not inspect.isclass(cls):
        # Fallback: grab the first class defined in the module
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ == module.__name__:
                cls = obj
                expected_class_name = obj.__name__
                break

    if cls is None:
        # No usable class – skip registration
        return None

    # Extract the docstring from the source file (not the class __doc__)
    file_path = pathlib.Path(module.__file__).with_suffix(".py")
    description = _extract_docstring(file_path)

    return AgentInfo(
        name=expected_class_name,
        description=description,
        cls=cls,
        module_path=module_name,
    )

def load_all_agents() -> Dict[str, AgentInfo]:
    """
    Populate (or refresh) the global ``_AGENT_REGISTRY`` and return it.

    The function is idempotent – subsequent calls simply return the cached
    dictionary unless you explicitly clear ``_AGENT_REGISTRY``.
    """
    global _AGENT_REGISTRY
    if _AGENT_REGISTRY:          # already loaded
        return _AGENT_REGISTRY

    # NOTE: the package name must be fully qualified so Python can find it
    package_name = "mas.agents"
    package = importlib.import_module(package_name)

    # Walk through all sub‑modules in the package
    for _, mod_name, is_pkg in pkgutil.iter_modules(package.__path__, package_name + "."):
        if is_pkg:
            continue
        if not mod_name.endswith("_agent"):
            continue

        info = _load_agent_module(mod_name)
        if info:
            _AGENT_REGISTRY[info.name] = info

    return _AGENT_REGISTRY

def get_agent(name: str) -> Optional[AgentInfo]:
    """
    Retrieve a single ``AgentInfo`` by its registered name.
    Returns ``None`` if the name is unknown.
    """
    registry = load_all_agents()
    return registry.get(name)

def list_agents() -> List[AgentInfo]:
    """
    Return a list of all registered agents (sorted alphabetically by name).
    """
    return sorted(load_all_agents().values(), key=lambda a: a.name)

# ----------------------------------------------------------------------
# Helper for the Planner – produce a JSON‑compatible description list
# ----------------------------------------------------------------------
def agents_description_for_prompt() -> str:
    """
    Returns a nicely formatted multi‑line string that can be dropped
    directly into the LLM prompt used by the ``ModuleMatcher`` agent.

    Example output:

    ```
    - PopulationSelector: defines patient inclusion/exclusion.
    - ImagingProtocolDesigner: designs imaging acquisition settings.
    - SegmentationModule: automates ROI segmentation.
    - RadiomicsExtractor: extracts features.
    - ModelingModule: trains ML models for prediction.
    - ValidationModule: performs internal/external validation.
    ```
    """
    lines = []
    for info in list_agents():
        # Use the first sentence of the docstring if possible
        first_sentence = info.description.split("\n")[0].strip()
        lines.append(f"- {info.name}: {first_sentence}")
    return "\n".join(lines)


# --------------------------------------------------------------
# Self‑test – runs when this module is executed as a script
# --------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import logging

    # Configure a simple logger for the self‑test
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)

    try:
        # Load the registry (populates the internal cache)
        agents = load_all_agents()

        # Basic sanity‑check
        if not agents:
            raise RuntimeError(
                "Agent registry is empty – no *_agent.py modules were discovered."
            )

        # Print a friendly summary
        logger.info("✅ Agent registry loaded successfully. Discovered agents:")
        for info in sorted(agents.values(), key=lambda a: a.name):
            # Show only the first sentence of the docstring for readability
            first_sentence = info.description.split("\n")[0].strip()
            print(f"- {info.name}: {first_sentence}")

        # Example of using the helper that formats the description for LLM prompts
        print("\n--- Prompt‑ready description block ---")
        print(agents_description_for_prompt())

    except Exception as exc:
        logger.error("Agent registry self‑test failed: %s", exc)
        sys.exit(1)

    # Exit cleanly
    sys.exit(0)