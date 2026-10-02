from pydantic import Field
from typing import Annotated
from ..._toolkit import tool
from ...common.manifests import READ_ONLY
from .. import _api
from ...common.emit import emit


@tool(
    visibility={"agent", "main"},
    **READ_ONLY,
)
async def get_volume(
    volume_series_instance_uid: Annotated[str | None, Field(description="The unique identifier (Series Instance UID) of the volume. If provided, fetches a single detailed record. If omitted, fetches a list of all volumes.")] = None,
    skip: Annotated[int, Field(description="Pagination offset: The number of volume records to skip before starting the list.")] = 0,
    limit: Annotated[int, Field(description="Pagination limit: The maximum number of volume records to return in one request (max 100).")] = 100
):
    """
    Retrieves volume series information from the imaging database.

    Use this tool in two scenarios:
    1. To find details for a specific volume when you know its 'volume_series_instance_uid'.
    2. To browse or list multiple volumes when no specific UID is known (uses pagination).
    """
    emit('tool_log', 'Checking available study descriptions...')
    if volume_series_instance_uid is not None:
        result = await _api.read(f"/volumes/{volume_series_instance_uid}")
    else:
        result = await _api.read("/volumes/", params={"skip": skip, "limit": limit})
    return result
    
@tool(
    visibility={"agent", "main"},
    **READ_ONLY,
)
async def get_volumes_by_study(
    study_uid: Annotated[str, Field(description="The unique identifier of the medical study. This is required to fetch all image volumes associated with that specific study.")]
):
    """
    Retrieves all image volumes associated with a specific medical study.

    you can use this tool in this specific scenario:
    1. To retrieve a list of scan volumes when you have a known 'study_uid' (e.g., "Show me the volumes for Study 12345").
    """
    return await _api.read(f"/volumes/by_study/{study_uid}")
