from typing import Annotated

from pydantic import Field

from .._toolkit import tool
from ..common.manifests import READ_ONLY
from . import _api


@tool(
    visibility={"agent", "main"},
    data_scopes={"phi_read"},
    timeout_s=45,
    **READ_ONLY,
)
async def get_all_studies_info(
    skip: Annotated[int, Field(ge=0, description="Pagination offset: the number of studies to skip.")] = 0,
    limit: Annotated[int, Field(ge=1, le=100, description="Pagination limit: the maximum number of studies to return (max 100).")] = 100,
):
    """
    This tool returns the studies info that could be used in
    specifying the population that is targeted in the study, one page at a time
    """
    return await _api.get_json("/studies/", params={"skip": skip, "limit": limit})
