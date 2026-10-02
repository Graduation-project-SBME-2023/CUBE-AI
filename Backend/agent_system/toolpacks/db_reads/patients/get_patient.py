from ..._toolkit import tool
from ...common import views
from ...common.manifests import READ_ONLY
from .. import _api
from typing import Annotated
from pydantic import Field
from ...common.emit import emit


@tool(
    visibility={"agent", "main"},
    data_scopes={"phi_read"},
    timeout_s=45,
    **READ_ONLY,
    on_result=views.on_population_tool,
)
async def get_patient(
    patient_id: Annotated[int | None, Field(description="The unique ID of the patient. If provided, fetches a single detailed record. If omitted, fetches a list of all patients.")] = None,
    skip: Annotated[int, Field(description="Pagination offset: The number of patient records to skip before starting the list.")] = 0,
    limit: Annotated[int, Field(description="Pagination limit: The maximum number of patient records to return in one request (max 100).")] = 100
):
    """
    Retrieves patient information from the database. 
    Use this tool in two scenarios:
    1. To find details for a specific person when you know their ID.
    2. To browse or list multiple patients when no specific ID is known (uses pagination).
    """
    emit("tool_log", "Checking patients' demographics...")
    if patient_id is not None:
        result = await _api.read(f"/patients/{patient_id}")
    else:
        result = await _api.read("/patients/", params={"skip": skip, "limit": limit})
    return result
