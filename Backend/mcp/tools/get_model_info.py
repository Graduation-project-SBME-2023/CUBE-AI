from . import mcp
import requests


@mcp.tool()
async def get_model_info():
    """
    This tool fetches all available model information from the MONAI Label server.
    Returns:
        A list of models, each with:
            - name: Model name
            - description: Brief description of the model
            - configuration: Model-specific configuration details
    Useful for agents to discover available models and their capabilities before running inference or segmentation tasks.
    """
    response = requests.get("http://localhost:8000/models")
    return response
