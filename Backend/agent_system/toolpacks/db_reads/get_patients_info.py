from .._toolkit import tool
from ..common.manifests import READ_ONLY
import httpx
from ..common.service_urls import CONTROL_API_URL

base_url = CONTROL_API_URL


@tool(
    visibility={"agent", "main"},
    **READ_ONLY,
)
async def get_all_patients_info():
    """
    This tool returns all the patients info that could be used in 
    specifying the population that is targeted in the study
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(base_url + "/patients")
    response.raise_for_status()
    return response.json()
