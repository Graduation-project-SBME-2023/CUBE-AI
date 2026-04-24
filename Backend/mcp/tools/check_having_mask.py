from . import mcp
import requests
import os


@mcp.tool()
async def check_having_mask(volume_uid: str):
    """
    Checks if a segmentation mask exists for the given volume SeriesInstanceUID (volume_uid).
    Calls the endpoint /segmentations/by-volume/{volume_uid} and returns the list of segmentations.
    """
    base_url = os.environ.get("MONAI_URL", "http://localhost:8000")
    url = f"{base_url}/segmentations/by-volume/{volume_uid}"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    else:
        return []
