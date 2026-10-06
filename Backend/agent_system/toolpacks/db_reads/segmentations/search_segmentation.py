from typing import Annotated

from pydantic import Field

from ..._toolkit import tool
from ...common.manifests import READ_ONLY
from .. import _api
from ..result_shape import keep_model_id


@tool(
    visibility={"agent", "main"},
    data_scopes={"phi_read"},
    timeout_s=45,
    **READ_ONLY,
)
async def search_segmentation(
    series_description: Annotated[str | None, Field(description="Text from the DICOM SeriesDescription, which usually names the anatomical structure or region (e.g., 'Liver', 'Tumor').")] = None,
    generation_method: Annotated[str | None, Field(description="How the mask was generated, as recorded on the row (e.g., 'modification', 'unknown').")] = None,
    skip: _api.Skip = 0,
    limit: _api.Limit = _api.MAX_PAGE,
):
    """Searches for segmentation labels using specific criteria."""
    params = {
        **_api.page(skip, limit),
        **_api.query(series_description=series_description, generation_method=generation_method),
    }
    return await _api.read("/segmentation-masks/search/", params=params, shape=keep_model_id)
