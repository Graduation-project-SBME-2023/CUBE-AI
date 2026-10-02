from ..._toolkit import tool
from ...common.manifests import READ_ONLY
from .. import _api


@tool(
    visibility={"agent", "main"},
    timeout_s=45,
    **READ_ONLY,
)
async def get_segmentation_stats():
    """
    Retrieves the breakdown of segmentation counts.

    Use this tool in this specific scenario:
    1. To get a statistical summary showing how many segmentations exist for each 'AI Model' and for each 'Segmentation Type' (e.g., "Give me the count of segmentations by model and organ type").
    """
    return await _api.read("/segmentation-masks/stats/")
