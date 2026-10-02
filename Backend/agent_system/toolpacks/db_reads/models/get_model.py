from ..._toolkit import tool
from ...common.manifests import READ_ONLY
from .. import _api
from typing import Annotated
from pydantic import Field

from ..result_shape import keep_model_id


@tool(
    visibility={"agent", "main"},
    **READ_ONLY,
)
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
    if model_id is not None:
        return await _api.read(f"/inference-models/{model_id}", shape=keep_model_id)
    return await _api.read(
        "/inference-models/", params={"skip": skip, "limit": limit}, shape=keep_model_id
    )
