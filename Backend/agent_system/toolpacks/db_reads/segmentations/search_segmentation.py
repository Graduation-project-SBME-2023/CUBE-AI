from ..._toolkit import tool
from ...common.manifests import READ_ONLY
from typing import Annotated
from pydantic import Field
import httpx
import os
from ...common.service_urls import CONTROL_API_URL

from ..result_shape import keep_model_id

base_url = CONTROL_API_URL

@tool(
    visibility={"agent", "main"},
    **READ_ONLY,
)
async def search_segmentation(
    series_description: Annotated[str | None, Field(description="Text from the DICOM SeriesDescription, which usually names the anatomical structure or region (e.g., 'Liver', 'Tumor').")] = None,
    generation_method: Annotated[str | None, Field(description="How the mask was generated, as recorded on the row (e.g., 'modification', 'unknown').")] = None,
    skip: Annotated[int, Field(description="Pagination offset: The number of records to skip.")] = 0,
    limit: Annotated[int, Field(description="Pagination limit: The maximum number of records to return (max 100).")] = 100
):
    """Searches for segmentation labels using specific criteria."""
    async with httpx.AsyncClient() as client:
        # Initialize params with pagination defaults
        params = {"skip": skip, "limit": limit}
        if series_description is not None:
            params["series_description"] = series_description
        if generation_method is not None:
            params["generation_method"] = generation_method

        response = await client.get(
            base_url + "/segmentation-masks/search/",
            params=params
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return {"error": "Invalid JSON returned", "raw": response.text}

        return {"data": keep_model_id(data)}
