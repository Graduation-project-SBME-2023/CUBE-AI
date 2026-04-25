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

    payload = TextPayload(message=f'Fetching models info')
    start_event = ToolEvent('tool_started', 'text', payload)
    log_tool_state(start_event)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{base_url}/models/")
            response.raise_for_status()
            models = response.json()
    except Exception as e:
        return {"error": str(e)}

    def infer_capabilities(model):
        name = model.get("model_name", "").lower()
        return {
            "points": any(x in name for x in ["vista", "sam", "nninteractive"]),
            "boxes": "sam" in name,
            "multi_label": any(x in name for x in ["vista", "sam"]),
        }

    formatted = []
    for m in models:
        types = m.get("types", [])
        formatted.append({
            "name": m.get("model_name", "unknown"),
            "types": types,
            "is_automatic": "automatic" in types,
            "is_interactive": "interactive" in types,
            "dice_score": m.get("dice_score", 0.0),
            "supports": infer_capabilities(m),
            "description": m.get("description", "")
        })

    payload = TextPayload(message=f"Fetched {len(formatted)} models")
    log_tool_state(ToolEvent("tool_finished", "text", payload))

    return formatted