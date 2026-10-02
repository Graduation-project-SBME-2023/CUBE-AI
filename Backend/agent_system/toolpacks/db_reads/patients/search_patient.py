from ..._toolkit import tool
from ...common.manifests import READ_ONLY
from .. import _api
from typing import Annotated
from pydantic import Field


@tool(
    visibility={"agent", "main"},
    data_scopes={"phi_read"},
    timeout_s=45,
    **READ_ONLY,
)
async def search_patient(
    name: Annotated[str|None, Field(description="The name (or partial name) of the patient to search for.")] = None,
    gender: Annotated[str|None, Field(description="The gender of the patient. Strictly accepts only 'M' for Male or 'F' for Female.")] = None
):
    """
    Searches for patient records using demographic details.

    Use this tool in this specific scenario:
    1. To lookup a patient when their unique ID is unknown, but you have their name and gender (e.g., "Find the male patient named John").
    """
    params = {}
    if name is not None:
        params["name"] = str(name)
    if gender is not None:
        params["gender"] = gender
    return await _api.read("/patients/search", params=params)
