from ... import mcp 
from typing import Annotated
from pydantic import Field
import httpx
import os
from service_urls import CONTROL_API_URL

base_url = CONTROL_API_URL


@mcp.tool()
async def search_patient(
    name: Annotated[str|None, Field(description="The name (or partial name) of the patient to search for.")] = None,
    gender: Annotated[str|None, Field(description="The gender of the patient. Strictly accepts only 'M' for Male or 'F' for Female.")] = None
):
    """
    Searches for patient records using demographic details.

    Use this tool in this specific scenario:
    1. To lookup a patient when their unique ID is unknown, but you have their name and gender (e.g., "Find the male patient named John").
    """
    async with httpx.AsyncClient() as client:
        params = {}
        if name is not None:
            params["name"] = str(name)
        if gender is not None:
            params["gender"] = gender
        response = await client.get(
            f"{base_url}/patients/search",
            params=params
            )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return {"error": "Invalid JSON returned", "raw": response.text}

        return {"data": data}
