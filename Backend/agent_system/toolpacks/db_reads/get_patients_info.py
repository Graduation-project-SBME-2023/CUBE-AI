from . import mcp
import httpx
from service_urls import CONTROL_API_URL

base_url = CONTROL_API_URL


@mcp.tool()
async def get_all_patients_info():
    """
    This tool returns all the patients info that could be used in 
    specifying the population that is targeted in the study
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(base_url + "/patients")
    response.raise_for_status()
    return response.json()
