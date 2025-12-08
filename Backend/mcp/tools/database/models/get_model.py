from ... import mcp
import httpx
from typing import Annotated
from pydantic import Field


@mcp.tool()
async def get_model(
    model_id: Annotated[int | None, Field(description="The unique identifier of the AI model. If provided, fetches a single detailed record. If omitted, fetches a list of all models.")] = None,
    skip: Annotated[int, Field(description="Pagination offset: The number of model records to skip before starting the list.")] = 0,
    limit: Annotated[int, Field(description="Pagination limit: The maximum number of model records to return in one request (max 100).")] = 100
):
    """
    Retrieves information about available AI models in the system.

    Use this tool in two scenarios:
    1. To find details for a specific model when you know its unique 'model_id'.
    2. To browse or list multiple models when no specific ID is known (uses pagination).
    """
    async with httpx.AsyncClient() as client:
        if model_id is not None:
            response = await client.get(f"http://localhost:8000/models/{model_id}")
        else:
            response = await client.get(
                "http://localhost:8000/models/",
                params={"skip": skip, "limit": limit}
            )

        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return {"error": "Invalid JSON returned", "raw": response.text}

        return {"data": data}