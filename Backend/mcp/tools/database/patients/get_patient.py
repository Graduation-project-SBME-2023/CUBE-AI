import httpx
from ... import mcp
from typing import Annotated
from pydantic import Field
from tool_logging import ToolEvent, log_tool_state, TextPayload


@mcp.tool()
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
        payload = TextPayload(message='Checking patients\' demographics...')
        start_event = ToolEvent('tool_started', 'study_population_planner_agent', 'text', payload)
        log_tool_state(start_event)
        log_event = ToolEvent('tool_progress', 'study_population_planner_agent', 'text', payload)
        log_tool_state(log_event)
        if patient_id is not None:
            response = await client.get(f"http://localhost:8000/patients/{patient_id}")
        else:
            response = await client.get(
                "http://localhost:8000/patients/",
                params={"skip": skip, "limit": limit}
            )

        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return {"error": "Invalid JSON returned", "raw": response.text}

        payload = TextPayload(message='Checking patients\' demographics...')
        finished_event = ToolEvent('tool_finished', 'study_population_planner_agent', 'text', payload)
        log_tool_state(finished_event)
        return {"data": data}