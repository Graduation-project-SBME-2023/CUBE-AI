import httpx
from ..._toolkit import tool
from ...common.manifests import READ_ONLY
from typing import Annotated
from pydantic import Field
from ...common.emit import emit
import os
from ...common.service_urls import CONTROL_API_URL


base_url = CONTROL_API_URL


@tool(
    visibility={"agent", "main"},
    **READ_ONLY,
)
async def get_patient(
    patient_id: Annotated[int | None, Field(description="The unique ID of the patient. If provided, fetches a single detailed record. If omitted, fetches a list of all patients.")] = None,
    skip: Annotated[int, Field(description="Pagination offset: The number of patient records to skip before starting the list.")] = 0,
    limit: Annotated[int, Field(description="Pagination limit: The maximum number of patient records to return in one request (max 100).")] = 100
):
    """
    Retrieves patient information from the database. 
    Use this tool in two scenarios:
    1. To find details for a specific person when you know their ID.
    2. To browse or list multiple patients when no specific ID is known (uses pagination).
    """
    async with httpx.AsyncClient() as client:
        emit('tool_started', 'Checking patients\' demographics...')
        emit('tool_progress', 'Checking patients\' demographics...')
        if patient_id is not None:
            response = await client.get(f"{base_url}/patients/{patient_id}")
        else:
            response = await client.get(
                base_url + "/patients/",
                params={"skip": skip, "limit": limit}
            )

        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return {"error": "Invalid JSON returned", "raw": response.text}

        emit('tool_finished', 'Checking patients\' demographics...')
        return {"data": data}
