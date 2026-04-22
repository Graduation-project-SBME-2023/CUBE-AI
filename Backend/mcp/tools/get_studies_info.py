from . import mcp
import requests
import os


@mcp.tool()
async def get_all_studies_info():
    """
    This tool returns all the studies info that could be used in 
    specifying the population that is targeted in the study
    """
    base_url = os.environ.get("MONAI_URL", "http://localhost:8000")
    response = requests.get(base_url + "/studies")
    return response.json()