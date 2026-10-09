from typing import Annotated

from pydantic import Field

from ..._toolkit import tool
from ...common.manifests import READ_ONLY
from .. import _api


@tool(
    visibility={"agent", "main"},
    data_scopes={"phi_read"},
    timeout_s=45,
    **READ_ONLY,
)
async def search_volumes(
    modality: Annotated[str | None, Field(description="The imaging modality to filter volumes by (e.g., 'CT', 'MRI'). If omitted, includes all modalities.")] = None,
    keyword: Annotated[str | None, Field(description="A specific keyword to search for within the volume descriptions.")] = None,
):
    """Searches for image volumes based on modality or descriptive keywords."""
    return await _api.read("/volumes/search/", params=_api.query(modality=modality, keyword=keyword))
