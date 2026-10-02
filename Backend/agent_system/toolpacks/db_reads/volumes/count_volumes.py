from ..._toolkit import tool
from ...common.manifests import READ_ONLY
from .. import _api
from pydantic import Field
from typing import Annotated


@tool(
    visibility={"agent", "main"},
    **READ_ONLY,
)
async def count_volumes_in_study(
    study_uid: Annotated[str, Field(description="The unique identifier of the medical study. This is required to count the number of volumes associated with it.")]
):
    """
    Counts the total number of image volumes associated with a specific study.

    Use this tool in this specific scenario:
    1. To quickly get the quantity of volumes for a study using the 'study_uid' (e.g., "How many volumes are in Study X?") without retrieving the full list of volume details.
    """
    return await _api.read(f"/volumes/count/{study_uid}")
