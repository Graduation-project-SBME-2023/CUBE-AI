from ... import mcp 
from typing import Annotated
from pydantic import Field
import httpx

@mcp.tool()
async def search_volumes(
    modality: Annotated[str | None, Field(description="The imaging modality to filter volumes by (e.g., 'CT', 'MRI'). If omitted, includes all modalities.")] = None,
    keyword: Annotated[str | None, Field(description="A specific keyword to search for within the volume descriptions.")] = None
):
    """
    Searches for image volumes based on modality or descriptive keywords.

    Use this tool in these scenarios:
    1. To find volumes of a specific type (e.g., "Find all MRI volumes") using 'modality'.
    2. To search for volumes containing specific terms in their description using 'keyword'.
    3. To narrow down results using both filters (e.g., "Show me CT volumes mentioning 'lung'").
    """
    async with httpx.AsyncClient() as client:
        params = {}
        if modality is not None:
            params["modality"] = str(modality)
        if keyword is not None:
            params["keyword"] = keyword
            
        response = await client.get(
            "http://localhost:8000/volumes/search/",
            params=params
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return {"error": "Invalid JSON returned", "raw": response.text}

        return {"data": data}