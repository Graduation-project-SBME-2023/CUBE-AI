from ... import mcp 
from typing import Annotated
from pydantic import Field
import httpx
import os

base_url = os.environ.get("MONAI_URL", "http://localhost:8000")

@mcp.tool()
async def search_segmentation(
    segmentation_type: Annotated[str | None, Field(description="The anatomical structure or region intended to be segmented (e.g., 'Liver', 'Tumor').")] = None,
    method: Annotated[str | None, Field(description="The technique used to generate the segmentation (e.g., 'Manual', 'AI', 'Semi-automatic').")] = None,
    skip: Annotated[int, Field(description="Pagination offset: The number of records to skip.")] = 0,
    limit: Annotated[int, Field(description="Pagination limit: The maximum number of records to return (max 100).")] = 100
):
    """
    Searches for segmentation labels using specific criteria.

    Use this tool in these scenarios:
    1. To find segmentations of a specific organ or structure using 'segmentation_type' (e.g., "Show me liver segmentations").
    2. To filter results based on how they were created using 'method' (e.g., "Show me manual segmentations").
    3. To combine both filters for precise lookups (uses pagination).
    """
    async with httpx.AsyncClient() as client:
        # Initialize params with pagination defaults
        params = {"skip": skip, "limit": limit}
        if segmentation_type is not None:
            params["segmentation_type"] = segmentation_type
        if method is not None:
            params["method"] = method
            params["skip"] = skip
            params["limit"] = limit
            
        response = await client.get(
            base_url + "/segmentations/search/",
            params=params
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return {"error": "Invalid JSON returned", "raw": response.text}

        return {"data": data}