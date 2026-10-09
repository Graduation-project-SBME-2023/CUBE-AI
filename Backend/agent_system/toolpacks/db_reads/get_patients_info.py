from .._toolkit import tool
from ..common.manifests import READ_ONLY
from . import _api


@tool(
    visibility={"agent", "main"},
    data_scopes={"phi_read"},
    timeout_s=45,
    **READ_ONLY,
)
async def get_all_patients_info(skip: _api.Skip = 0, limit: _api.Limit = _api.MAX_PAGE):
    """
    This tool returns the patients info that could be used in
    specifying the population that is targeted in the study, one page at a time
    """
    return await _api.get_json("/patients/", params=_api.page(skip, limit))
