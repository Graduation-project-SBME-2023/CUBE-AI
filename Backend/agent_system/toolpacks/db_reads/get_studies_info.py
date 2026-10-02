from . import mcp
import httpx
from service_urls import CONTROL_API_URL


@mcp.tool()
async def get_all_studies_info():
    """
    This tool returns all the studies info that could be used in 
    specifying the population that is targeted in the study
    """
    base_url = CONTROL_API_URL
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(base_url + "/studies")
    response.raise_for_status()
    return response.json()
