from ... import mcp 
from typing import Annotated
from pydantic import Field
import httpx
import os
from storage_paths import workspace_subpath
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
    words_to_include: Annotated[list[str], Field(description="List of keywords that MUST be present in the volume description or body part examined.")],
    words_to_exclude: Annotated[list[str] | None, Field(description="Optional list of keywords that MUST NOT be present in the volume description.")] = None
) -> dict:
    """
    Search for volumes based on included and excluded words in the series description
    and body_part_examined fields, and save the resulting list of UIDs to the workspace config.

    Returns detailed volume info (uid, series_description, body_part_examined, modality)
    so you can verify the anatomy of each volume matches your hypothesis before proceeding.

    Use this tool in these scenarios:
    1. To find volumes matching specific descriptive terms (e.g., ["brain", "mri"]).
    2. To refine searches by filtering out unwanted terms (e.g., excluding ["contrast"]).

    IMPORTANT: After receiving the results, inspect the body_part_examined and
    series_description fields to confirm the volumes match your hypothesis anatomy.
    If the volumes belong to a different body region, do NOT proceed with them.
    """

    volume_details = []  # list of dicts from the API
    uids_list = []       # flat list of UIDs for config.json

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
            volume_details = response.json()  # list of dicts
            uids_list = [v["uid"] for v in volume_details]

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
        json_path = workspace_subpath(workspace_dir, "config.json")

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

            # Save candidate UIDs separately under 'candidate_datasetlist' so un-validated studies are not displayed in the UI before validate_population_anatomy completes
            json_data["candidate_datasetlist"] = uids_list
            json_data["anatomy_validated"] = False

            # Save the updated JSON back to the file
            with open(json_path, 'w', encoding='utf-8') as file:
                json.dump(json_data, file, indent=4)

            file_saved = True
            file_status_msg = f"Successfully saved candidate volumes in {json_path}."

        except Exception as e:
            # Catch the error but DO NOT return, allowing the broadcast to proceed
            file_status_msg = f"Failed to save to disk: {str(e)}"

    # 3. Return results without broadcasting via WebSocket
    # (Broadcast will occur AFTER validate_population_anatomy executes)
    return {
        "status": "success",
        "message": f"Found {len(uids_list)} candidate volumes. {file_status_msg} Now call validate_population_anatomy to confirm anatomical alignment.",
        "count": len(uids_list),
        "volumes_detail": volume_details,
        "saved_path": str(json_path) if file_saved else "N/A"
    }
