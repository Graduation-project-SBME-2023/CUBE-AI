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
async def get_patient(
    patient_id: Annotated[int | None, Field(description="The unique ID of the patient. If provided, fetches a single detailed record. If omitted, fetches a list of all patients.")] = None,
    skip: _api.Skip = 0,
    limit: _api.Limit = _api.MAX_PAGE,
):
    """
    Retrieves patient information from the database.
    Use this tool in two scenarios:
    1. To find details for a specific person when you know their ID.
    2. To browse or list multiple patients when no specific ID is known (uses pagination).
    """
    emit("tool_log", "Checking patients' demographics...")
    if patient_id is not None:
        return await _api.read(f"/patients/{patient_id}")
    return await _api.read("/patients/", params=_api.page(skip, limit))
