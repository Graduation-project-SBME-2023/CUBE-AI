from . import mcp
import requests

@mcp.tool()
async def get_model_info():
    """
    Returns a structured list of available segmentation models
    with their capabilities so the agent can choose the right one.
    """
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

    return formatted
