from . import mcp
import requests

@mcp.tool()
async def run_segmentation(model: str, series_instance_uid: str, params: dict):
    """
    This tool requests segmentation from the MONAI Label server.
    Args:
        model: The segmentation model name (e.g., 'vista', 'sam', 'nninteractive').
        series_instance_uid: The SeriesInstanceUID for the image to segment.
        params: Dictionary of inference parameters (e.g., points, boxes).
    Returns:
        Segmentation result from MONAI Label server.
    """
    url = "http://localhost:8000/infer"
    payload = {
        "model": model,
        "series_instance_uid": series_instance_uid,
        "params": params
    }
    response = requests.post(url, json=payload)
    return response.json()