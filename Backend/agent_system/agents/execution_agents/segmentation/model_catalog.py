"""Authoritative model catalog for segmentation-agent execution."""

import os
from typing import Literal


ExecutionPath = Literal["automatic", "interactive"]

# Add future segmentation-agent models here with their required execution path.
SEGMENTATION_MODEL_PATHS: dict[str, ExecutionPath] = {
    "voxtell": "interactive",
}


def get_enabled_segmentation_model_paths() -> dict[str, ExecutionPath]:
    """Return the configured subset of models known to the agent."""
    configured = os.environ.get("SEGMENTATION_AGENT_MODELS")
    if configured is None:
        return dict(SEGMENTATION_MODEL_PATHS)

    requested = {
        name.strip().casefold()
        for name in configured.split(",")
        if name.strip()
    }
    return {
        name: execution_path
        for name, execution_path in SEGMENTATION_MODEL_PATHS.items()
        if name.casefold() in requested
    }
