from . import mcp
import httpx

from service_urls import CONTROL_API_URL

from .database.result_shape import keep_model_id


@mcp.tool()
async def check_having_mask(volume_uid: str):
    """
    Checks if a segmentation mask exists for the given volume SeriesInstanceUID (volume_uid).
    Calls the endpoint /segmentation-masks/by-volume/{volume_uid} and returns the list of segmentations.
    """
    url = f"{CONTROL_API_URL}/segmentation-masks/by-volume/{volume_uid}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
    if response.status_code == 200:
        return keep_model_id(response.json())
    else:
        return []
