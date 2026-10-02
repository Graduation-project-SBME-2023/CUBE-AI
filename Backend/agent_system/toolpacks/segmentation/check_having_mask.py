from .._toolkit import tool
from ..common.manifests import READ_ONLY
import httpx

from ..common.service_urls import CONTROL_API_URL

from ..db_reads.result_shape import keep_model_id


@tool(
    visibility={"agent", "main"},
    **READ_ONLY,
)
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
