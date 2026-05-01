from . import mcp
import requests
import sys
import os
import json
import httpx
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))
from tool_logging import ToolEvent, log_tool_state, TextPayload

base_url = os.environ.get("MONAI_URL", "http://localhost:8000")

@mcp.tool()
async def list_models():

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

    for m in models:

        formatted.append({
            "name": m.get("name"),
            "type": m.get("type"),  # automatic / interactive
            "modality": m.get("modality", "unknown"),
            "anatomy": m.get("anatomy", []),
            "labels": m.get("labels", {}),
            "params_schema": m.get("params_schema", None),
            "description": m.get("description", "")
        })

    payload = TextPayload(message=f"Fetched {len(formatted)} models")
    log_tool_state(ToolEvent("tool_finished", "text", payload))

    return json.dumps(formatted)
