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
async def search_segmentation(
    series_description: Annotated[str | None, Field(description="Text from the DICOM SeriesDescription, which usually names the anatomical structure or region (e.g., 'Liver', 'Tumor').")] = None,
    generation_method: Annotated[str | None, Field(description="How the mask was generated, as recorded on the row (e.g., 'modification', 'unknown').")] = None,
    skip: Annotated[int, Field(description="Pagination offset: The number of records to skip.")] = 0,
    limit: Annotated[int, Field(description="Pagination limit: The maximum number of records to return (max 100).")] = 100
):
    """Searches for segmentation labels using specific criteria."""
    params = {"skip": skip, "limit": limit}
    if series_description is not None:
        params["series_description"] = series_description
    if generation_method is not None:
        params["generation_method"] = generation_method
    return await _api.read("/segmentation-masks/search/", params=params, shape=keep_model_id)
