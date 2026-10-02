import httpx
from .. import mcp
import os
from service_urls import CONTROL_API_URL

# Assuming your mcp instance is imported here
# from your_mcp_server import mcp
base_url = CONTROL_API_URL

@mcp.tool()
async def get_unique_series_descriptions() -> dict:
    """
    Retrieve a list of all unique volume series descriptions available in the database.
    
    Use this tool to discover what descriptive terms actually exist in the dataset. 
    This is highly useful before performing a search to see exactly how protocols or 
    body parts are named (e.g., checking if it's 'CT Head' vs 'Head CT').
    """
    async with httpx.AsyncClient() as client:
        try:
            # Make the request to your new FastAPI endpoint
            response = await client.get(f"{base_url}/volumes/descriptions/unique/")
            response.raise_for_status()
            
            # Parse the list of strings
            descriptions = response.json()
            
            return {
                "status": "success",
                "count": len(descriptions),
                "unique_descriptions": descriptions
            }
            
        except httpx.HTTPStatusError as e:
             return {
                 "status": "error", 
                 "message": f"HTTP error occurred: {str(e)}"
             }
        except ValueError:
            return {
                "status": "error", 
                "message": "Invalid JSON returned from API", 
                "raw": response.text
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"An unexpected error occurred: {str(e)}"
            }
