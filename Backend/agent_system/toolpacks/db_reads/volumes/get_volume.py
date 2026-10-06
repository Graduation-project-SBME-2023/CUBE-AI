from typing import Annotated

from pydantic import Field

from ..._toolkit import tool
from ...common import views
from ...common.emit import emit
from ...common.manifests import READ_ONLY
from .. import _api


@tool(
    visibility={"agent", "main"},
    data_scopes={"phi_read"},
    timeout_s=45,
    **READ_ONLY,
    on_result=views.on_population_tool,
)
async def get_volume(
    volume_series_instance_uid: Annotated[str | None, Field(description="The unique identifier (Series Instance UID) of the volume. If provided, fetches a single detailed record. If omitted, fetches a list of all volumes.")] = None,
    skip: _api.Skip = 0,
    limit: _api.Limit = _api.MAX_PAGE,
):
    """
    Retrieves volume series information from the imaging database.

    Use this tool in two scenarios:
    1. To find details for a specific volume when you know its 'volume_series_instance_uid'.
    2. To browse or list multiple volumes when no specific UID is known (uses pagination).
    """
    emit("tool_log", "Checking available study descriptions...")
    if volume_series_instance_uid is not None:
        return await _api.read(f"/volumes/{_api.segment(volume_series_instance_uid)}")
    return await _api.read("/volumes/", params=_api.page(skip, limit))


@tool(
    visibility={"agent", "main"},
    data_scopes={"phi_read"},
    timeout_s=45,
    **READ_ONLY,
)
async def get_volumes_by_study(
    study_uid: Annotated[str, Field(description="The unique identifier of the medical study. This is required to fetch all image volumes associated with that specific study.")],
):
    """
    Retrieves all image volumes associated with a specific medical study.

    you can use this tool in this specific scenario:
    1. To retrieve a list of scan volumes when you have a known 'study_uid' (e.g., "Show me the volumes for Study 12345").
    """
    return await _api.read(f"/volumes/by_study/{_api.segment(study_uid)}")
