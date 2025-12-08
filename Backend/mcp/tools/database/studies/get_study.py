import httpx
from ... import mcp
from typing import Annotated
from pydantic import Field


@mcp.tool()
async def get_study(
    study_uid: Annotated[str | None, Field(description="The unique identifier of the medical study. If provided, fetches a single detailed record. If omitted, fetches a list of all studies.")] = None,
    skip: Annotated[int, Field(description="Pagination offset: The number of study records to skip before starting the list.")] = 0,
    limit: Annotated[int, Field(description="Pagination limit: The maximum number of study records to return in one request (max 100).")] = 100
):
    """
    Retrieves medical study records from the general database. 
    
    you can use this tool in two scenarios:
    1. To find details for a specific study when you know its unique UID.
    2. To browse or list multiple studies when no specific UID is known (uses pagination).
    """
    async with httpx.AsyncClient() as client:
        if study_uid is not None:
            response = await client.get(f"http://localhost:8000/studies/{study_uid}")
        else:
            response = await client.get(
                "http://localhost:8000/studies/",
                params={"skip": skip, "limit": limit}
            )

        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return {"error": "Invalid JSON returned", "raw": response.text}

        return {"data": data}
    
@mcp.tool()
async def get_study_by_patient(
    patient_id: Annotated[int, Field(description="The unique ID of the patient. This is required to fetch all medical studies associated with that specific individual.")]
):
    """
    Retrieves all studies associated with a specific patient.
    
    you can use this tool in this scenario:
    1. To retrieve a patient's full medical history or list of scans when you have the 'patient_id' (e.g., "Show me studies for Patient 101").
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://localhost:8000/studies/by_patient/{patient_id}")
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return {"error": "Invalid JSON returned", "raw": response.text}

        return {"data": data}