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
async def search_studies(
    modality: Annotated[str | None, Field(description="The imaging modality to filter by (e.g., 'CT', 'MRI', 'X-RAY'). If omitted, includes all modalities.")] = None,
    keyword: Annotated[str | None, Field(description="A specific keyword to search for within the study descriptions (e.g., 'chest', 'contrast').")] = None,
):
    """
    Searches for medical studies using filtering criteria.

    Use this tool in these scenarios:
    1. To find studies of a specific type (e.g., "Find all CT scans") using 'modality'.
    2. To search for studies containing specific terms in their description using 'keyword'.
    3. To narrow down results by using both filters (e.g., "Show me MRI studies mentioning 'brain'").
    """
    return await _api.read("/studies/search/", params=_api.query(modality=modality, keyword=keyword))
