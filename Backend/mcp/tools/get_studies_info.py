from . import mcp
import requests

@mcp.tool()
async def get_all_studies_info():
    """
    This tool returns all the studies info that could be used in 
    specifying the population that is targeted in the study
    """
    response = requests.get("http://localhost:8000/studies")
    return response.json()