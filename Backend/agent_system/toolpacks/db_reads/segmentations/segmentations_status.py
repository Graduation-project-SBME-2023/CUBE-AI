from ..._toolkit import tool
from ...common.manifests import READ_ONLY
import httpx
import os
from ...common.service_urls import CONTROL_API_URL

base_url = CONTROL_API_URL

@tool(
    visibility={"agent", "main"},
    **READ_ONLY,
)
async def get_segmentation_stats():
    """
    Retrieves the breakdown of segmentation counts.

    Use this tool in this specific scenario:
    1. To get a statistical summary showing how many segmentations exist for each 'AI Model' and for each 'Segmentation Type' (e.g., "Give me the count of segmentations by model and organ type").
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(base_url + "/segmentation-masks/stats/")
        
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return {"error": "Invalid JSON returned", "raw": response.text}

        return {"data": data}
