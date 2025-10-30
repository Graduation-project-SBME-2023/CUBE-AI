from . import mcp
import requests


@mcp.tool()
async def get_all_patients_info():
    """
    This tool returns all the patients info that could be used in 
    specifying the population that is targeted in the study
    """
    response = requests.get("http://localhost:8000/patients")
    return response