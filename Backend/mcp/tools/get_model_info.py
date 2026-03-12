from . import mcp
import requests
import sys
import os
import json
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))
from tool_logging import ToolEvent,log_tool_state, TextPayload

@mcp.tool()
async def get_model_info():
    """
    Returns a structured list of available segmentation models
    with their capabilities so the agent can choose the right one.
    """
    # start_event = ToolEvent('tool_started', 'segmentation_agent','json',{'tool_name':'get_model_info','progress':'fetching auto models info'})
    # log_tool_state(start_event)
    
    response = requests.get("http://localhost:8000/models")
    models = response.json()

   
    def infer_capabilities(model):
        name = model["model_name"].lower()

        return {
            "points": name in ["vista", "sam", "nninteractive"],
            "boxes": name in ["sam"],
            "multi_label": name in ["vista", "sam"]
        }

    formatted = [
        {
            "name": m["model_name"],
            "type": m["types"],   # ["automatic"], ["interactive"], or both
            "supports": infer_capabilities(m),
            "description": m["description"]
        }
        for m in models
    ]
    payload = json.dumps(formatted)

    # finish_event = ToolEvent(
    #     event_type='tool_finished',
    #     source='segmentation_agent',
    #     content_type='json',
    #     content=payload
    # )

    # log_tool_state(finish_event)

    # finish_event = ToolEvent('tool_finished', 'segmentation_agent', {'tool_name':'get_model_info','progress':'models fetched'})

    return payload
