from ..._toolkit import tool
from ...common.manifests import READ_ONLY
from pydantic import Field
from typing import Annotated
import httpx
import os
from ...common.service_urls import CONTROL_API_URL

base_url = CONTROL_API_URL

@tool(
    visibility={"agent", "main"},
    **READ_ONLY,
)
async def count_volumes_in_study(
    study_uid: Annotated[str, Field(description="The unique identifier of the medical study. This is required to count the number of volumes associated with it.")]
):
    """
    Counts the total number of image volumes associated with a specific study.

    Use this tool in this specific scenario:
    1. To quickly get the quantity of volumes for a study using the 'study_uid' (e.g., "How many volumes are in Study X?") without retrieving the full list of volume details.
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{base_url}/volumes/count/{study_uid}")
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return {"error": "Invalid JSON returned", "raw": response.text}

        return {"data": data}
