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
async def get_study(
    study_uid: Annotated[str | None, Field(description="The unique identifier of the medical study. If provided, fetches a single detailed record. If omitted, fetches a list of all studies.")] = None,
    skip: _api.Skip = 0,
    limit: _api.Limit = _api.MAX_PAGE,
):
    """
    Retrieves medical study records from the general database.

    you can use this tool in two scenarios:
    1. To find details for a specific study when you know its unique UID.
    2. To browse or list multiple studies when no specific UID is known (uses pagination).
    """
    if study_uid is not None:
        return await _api.read(f"/studies/{_api.segment(study_uid)}")
    return await _api.read("/studies/", params=_api.page(skip, limit))


@tool(
    visibility={"agent", "main"},
    data_scopes={"phi_read"},
    timeout_s=45,
    **READ_ONLY,
)
async def get_study_by_patient(
    patient_id: Annotated[int, Field(description="The unique ID of the patient. This is required to fetch all medical studies associated with that specific individual.")],
):
    """
    Retrieves all studies associated with a specific patient.

    you can use this tool in this scenario:
    1. To retrieve a patient's full medical history or list of scans when you have the 'patient_id' (e.g., "Show me studies for Patient 101").
    """
    return await _api.read(f"/studies/by_patient/{patient_id}")
