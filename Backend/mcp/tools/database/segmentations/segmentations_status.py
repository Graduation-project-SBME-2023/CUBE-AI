from ... import mcp 
import httpx
import os

base_url = os.environ.get("MONAI_URL", "http://localhost:8000")

@mcp.tool()
async def get_segmentation_stats():
    """
    Retrieves the breakdown of segmentation counts.

    Use this tool in this specific scenario:
    1. To get a statistical summary showing how many segmentations exist for each 'AI Model' and for each 'Segmentation Type' (e.g., "Give me the count of segmentations by model and organ type").
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(base_url + "/segmentations/stats/")
        
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return {"error": "Invalid JSON returned", "raw": response.text}

        return {"data": data}