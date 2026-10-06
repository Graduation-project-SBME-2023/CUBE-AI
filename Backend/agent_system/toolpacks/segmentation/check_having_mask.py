"""``check_having_mask``: the segmentation masks one volume already has."""

from typing import Annotated

from pydantic import Field

from .._toolkit import tool
from ..common.manifests import READ_ONLY
from ..db_reads.segmentations.get_segmentation import masks_for_volume


@tool(
    visibility={"agent", "main"},
    data_scopes={"phi_read"},
    timeout_s=45,
    **READ_ONLY,
)
async def check_having_mask(
    volume_uid: Annotated[str, Field(description="The volume's SeriesInstanceUID.")],
):
    """
    Checks if a segmentation mask exists for the given volume SeriesInstanceUID (volume_uid).
    Calls the endpoint /segmentation-masks/by-volume/{volume_uid} and returns the list of segmentations.
    """
    return await masks_for_volume(volume_uid)
