from typing import Annotated

from pydantic import Field

from ..._toolkit import tool
from ...common.manifests import READ_ONLY
from .. import _api
from ..result_shape import keep_model_id


@tool(
    visibility={"agent", "main"},
    timeout_s=45,
    **READ_ONLY,
)
async def get_model(
    model_id: Annotated[int | None, Field(description="The unique identifier of the AI model. If provided, fetches a single detailed record. If omitted, fetches a list of all models.")] = None,
    skip: _api.Skip = 0,
    limit: _api.Limit = _api.MAX_PAGE,
):
    """
    Retrieves information about available AI models in the system.

    Use this tool in two scenarios:
    1. To find details for a specific model when you know its unique 'model_id'.
    2. To browse or list multiple models when no specific ID is known (uses pagination).
    """
    if model_id is not None:
        return await _api.read(f"/inference-models/{model_id}", shape=keep_model_id)
    return await _api.read("/inference-models/", params=_api.page(skip, limit), shape=keep_model_id)
