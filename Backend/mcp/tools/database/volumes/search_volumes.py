from ... import mcp 
from typing import Annotated
from pydantic import Field
import httpx
import json
from pathlib import Path

@mcp.tool()
async def search_volumes(
    modality: Annotated[str | None, Field(description="The imaging modality to filter volumes by (e.g., 'CT', 'MRI'). If omitted, includes all modalities.")] = None,
    keyword: Annotated[str | None, Field(description="A specific keyword to search for within the volume descriptions.")] = None
):
    """
    Searches for image volumes based on modality or descriptive keywords.

    Use this tool in these scenarios:
    1. To find volumes of a specific type (e.g., "Find all MRI volumes") using 'modality'.
    2. To search for volumes containing specific terms in their description using 'keyword'.
    3. To narrow down results using both filters (e.g., "Show me CT volumes mentioning 'lung'").
    """
    async with httpx.AsyncClient() as client:
        params = {}
        if modality is not None:
            params["modality"] = str(modality)
        if keyword is not None:
            params["keyword"] = keyword
            
        response = await client.get(
            "http://localhost:8000/volumes/search/",
            params=params
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return {"error": "Invalid JSON returned", "raw": response.text}

        return {"data": data}
    
@mcp.tool() 
async def search_volumes_by_description(
    words_to_include: Annotated[list[str], Field(description="List of keywords that MUST be present in the volume description.")],
    words_to_exclude: Annotated[list[str] | None, Field(description="Optional list of keywords that MUST NOT be present in the volume description.")] = None
):
    """
    Search for volume UIDs based on included and excluded words in the series description,
    and save the resulting list of UIDs to the population workspace.

    Use this tool in these scenarios:
    1. To find volumes matching specific descriptive terms (e.g., ["brain", "mri"]).
    2. To refine searches by filtering out unwanted terms (e.g., excluding ["contrast"]).
    """
    # 1. Fetch the data from the FastAPI endpoint
    async with httpx.AsyncClient() as client:
        params = {
            "words_to_include": words_to_include
        }
        
        if words_to_exclude is not None:
            params["words_to_exclude"] = words_to_exclude
            
        response = await client.get(
            "http://localhost:8000/volumes/search/description/",
            params=params
        )
        response.raise_for_status()
        
        try:
            uids_list = response.json()
        except ValueError:
            return {"error": "Invalid JSON returned from API", "raw": response.text}

    # 2. Resolve the path to the population.json file
    current_file_path = Path(__file__).resolve()
    backend_dir = current_file_path.parent.parent.parent.parent.parent # Go up 4 levels
    json_path = backend_dir / "agent_system" / "workspace" / "populations" / "populations" / "population.json"

    # 3. Read existing JSON data if the file exists
    json_data = {}
    if json_path.exists():
        try:
            with open(json_path, 'r', encoding='utf-8') as file:
                json_data = json.load(file)
        except json.JSONDecodeError:
            json_data = {} 
    else:
        json_path.parent.mkdir(parents=True, exist_ok=True)

    # 4. Update the datasetlist with the newly found UIDs
    json_data["datasetlist"] = uids_list

    # 5. Save the updated JSON back to the file
    try:
        with open(json_path, 'w', encoding='utf-8') as file:
            json.dump(json_data, file, indent=4)
            
        return {
            "status": "success",
            "message": f"number of the {len(uids_list)}",
            "count": len(uids_list),
            "saved_path": str(json_path)
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Search succeeded, but failed to save to population.json: {str(e)}",
            "count": len(uids_list)
        }