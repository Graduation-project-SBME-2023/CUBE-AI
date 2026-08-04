from ... import mcp 
from typing import Annotated
from pydantic import Field
import httpx
import os
from service_urls import CONTROL_API_URL

base_url = CONTROL_API_URL
session_id = os.environ.get("SESSION_ID")

import json
from pathlib import Path
import os

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
            base_url + "/volumes/search/",
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
) -> dict:
    """
    Search for volume UIDs based on included and excluded words in the series description,
    and save the resulting list of UIDs to the workspace config.

    Use this tool in these scenarios:
    1. To find volumes matching specific descriptive terms (e.g., ["brain", "mri"]).
    2. To refine searches by filtering out unwanted terms (e.g., excluding ["contrast"]).
    """

    uids_list = []

    # 1. Fetch the data from the FastAPI endpoint
    async with httpx.AsyncClient() as client:
        params = {"words_to_include": words_to_include}
        if words_to_exclude is not None:
            params["words_to_exclude"] = words_to_exclude

        try:
            response = await client.get(
                base_url + "/volumes/search/description/",
                params=params
            )
            response.raise_for_status()
            uids_list = response.json()

        except httpx.HTTPStatusError as e:
            return {"status": "error", "message": f"HTTP error occurred: {str(e)}"}
        except ValueError:
            return {"status": "error", "message": "Invalid JSON returned from API", "raw": response.text}
        except Exception as e:
            return {"status": "error", "message": f"An unexpected API error occurred: {str(e)}"}

    # Setup file-saving variables
    workspace_dir = os.environ.get("WORKSPACE_DIR")
    json_path = None
    file_status_msg = "File not saved (WORKSPACE_DIR env variable not set)."
    file_saved = False

    # 2. Handle File Saving (Only if WORKSPACE_DIR exists)
    if workspace_dir:
        json_path = Path(workspace_dir) / "config.json"

        try:
            # Ensure the directory exists
            json_path.parent.mkdir(parents=True, exist_ok=True)

            # Read existing JSON data if the file exists
            json_data = {}
            if json_path.exists():
                try:
                    with open(json_path, 'r', encoding='utf-8') as file:
                        json_data = json.load(file)
                except json.JSONDecodeError:
                    json_data = {}

            # Update the datasetlist with the newly found UIDs
            json_data["datasetlist"] = uids_list

            # Save the updated JSON back to the file
            with open(json_path, 'w', encoding='utf-8') as file:
                json.dump(json_data, file, indent=4)

            file_saved = True
            file_status_msg = f"Successfully updated datasetlist in {json_path}"

        except Exception as e:
            # Catch the error but DO NOT return, allowing the broadcast to proceed
            file_status_msg = f"Failed to save to disk: {str(e)}"

    # 3. ALWAYS Broadcast to the Frontend
    try:
        async with httpx.AsyncClient() as broadcast_client:
            await broadcast_client.post(
                "http://localhost:8150/ws-broadcast",
                json={
                    "type": "population_output",
                    "payload": {
                        "tool": "search_volumes_by_description",
                        "status": "completed",
                        "count": len(uids_list),
                        "uid_list": uids_list,
                        "saved_path": str(json_path) if file_saved else "N/A"
                    },
                    "session_id": session_id
                }
            )

        return {
            "status": "success",
            "message": f"Found {len(uids_list)} volumes. Broadcast successful. {file_status_msg}",
            "count": len(uids_list),
            "saved_path": str(json_path) if file_saved else "N/A"
        }

    except Exception as e:
        # Hard error only if the broadcast itself fails
        return {
            "status": "error",
            "message": f"Search succeeded, but broadcast failed: {str(e)}. File status: {file_status_msg}",
            "count": len(uids_list)
        }
