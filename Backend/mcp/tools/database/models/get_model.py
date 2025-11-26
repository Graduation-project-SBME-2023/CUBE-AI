from ... import mcp
import httpx


@mcp.tool()
async def get_model(model_id: int | None = None, skip:int = 0, limit:int=100):
    """
    Retrieve model data from the FastAPI backend.

    This function acts as a unified HTTP client for the model-related API
    endpoints. It supports two modes:

    1. **Fetch a single model**
       - Triggered when `model_id` is provided.
       - Sends a GET request to: `GET /{model_id}`

    2. **Fetch multiple models with pagination**
       - Triggered when `model_id` is not provided.
       - Sends a GET request to: `GET /?skip={skip}&limit={limit}`

    Parameters
    ----------
    model_id : int, optional
        The ID of a specific model to retrieve. If provided, pagination
        parameters (`skip`, `limit`) are ignored.
    skip : int, default=0
        Number of records to skip when fetching multiple models.
    limit : int, default=100
        Maximum number of models to return when fetching multiple models.

    Returns
    -------
    dict or list
        - A single model object (dict) when `model_id` is provided.
        - A list of model objects when fetching multiple models.
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