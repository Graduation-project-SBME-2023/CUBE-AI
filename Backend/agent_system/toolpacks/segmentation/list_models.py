from .._toolkit import tool
from ..common.manifests import READ_ONLY
import requests
import sys
import os
import json
import httpx
from agent_system.agents.execution_agents.segmentation.model_catalog import (
    get_enabled_segmentation_model_paths,
)
from ..common.emit import ToolEvent, log_tool_state, TextPayload

base_url = os.environ.get(
    "INFERENCE_API_URL",
    os.environ.get("MONAI_URL", "http://localhost:8000"),
)

@tool(
    visibility={"agent", "main"},
    **READ_ONLY,
)
async def list_models():
    """List the enabled segmentation models on the inference service: name,
    type (automatic or interactive), modality, anatomy, labels, params schema
    and description."""

    payload = TextPayload(message="Fetching models info")
    log_tool_state(ToolEvent("tool_started", "text", payload))

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{base_url}/models/")
            response.raise_for_status()
            models = response.json()
    except Exception as e:
        return {"error": str(e)}

    formatted = []

    payload = TextPayload(message="Formatting models")
    log_tool_state(ToolEvent("tool_progress", "text", payload))

    enabled_model_names = {
        name.casefold()
        for name in get_enabled_segmentation_model_paths()
    }
    for m in models:
        name = str(m.get("name") or "").strip()
        if name.casefold() not in enabled_model_names:
            continue

        formatted.append({
            "name": name,
            "type": m.get("type"),  # automatic / interactive
            "modality": m.get("modality", "unknown"),
            "anatomy": m.get("anatomy", []),
            "labels": m.get("labels", {}),
            "params_schema": m.get("params_schema", None),
            "description": m.get("description", "")
        })

    model_word = "model" if len(formatted) == 1 else "models"
    payload = TextPayload(message=f"Fetched {len(formatted)} {model_word}")
    log_tool_state(ToolEvent("tool_finished", "text", payload))

    return json.dumps(formatted)
