from . import mcp
import requests
import os

base_url = os.environ.get("MONAI_URL", "http://localhost:8000")


@mcp.tool()
async def get_all_patients_info():
    """
    This tool returns all the patients info that could be used in 
    specifying the population that is targeted in the study
    """
    response = requests.get(base_url + "/patients")
    return response.json()