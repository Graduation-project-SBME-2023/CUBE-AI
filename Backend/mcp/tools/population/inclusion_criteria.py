import json
import httpx
import os
from pathlib import Path
from typing import Annotated
from pydantic import Field
from .. import mcp

@mcp.tool()
async def write_inclusion_criteria(
    inclusion_criteria: Annotated[list[str], Field(description="List of clinical inclusion criteria rules to define the population.")],
    exclusion_criteria: Annotated[list[str], Field(description="List of clinical exclusion criteria rules to filter out patients.")],
    modality: Annotated[str, Field(description="The imaging modality associated with these criteria (e.g., 'CT', 'MRI').")]
) -> dict:
    """
    Updates or creates a config.json file with inclusion/exclusion criteria.
    If WORKSPACE_DIR is unset, or if file saving fails, it still broadcasts to the frontend.
    """
    
    workspace_dir = os.environ.get("WORKSPACE_DIR")
    session_id = os.environ.get("SESSION_ID")
    json_path = None
    file_status_msg = "File not saved (WORKSPACE_DIR env variable not set)."
    file_saved = False

    # 1. Handle File Saving (Only if WORKSPACE_DIR exists)
    if workspace_dir:
        json_path = Path(workspace_dir) / "config.json"
        
        try:
            # Ensure the directory exists (creates if missing)
            json_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Read existing data if the file already exists
            data = {}
            if json_path.exists():
                try:
                    with open(json_path, 'r', encoding='utf-8') as file:
                        data = json.load(file)
                except json.JSONDecodeError:
                    data = {}

            # Update data
            data["inclusion_criteria"] = inclusion_criteria
            data["exclusion_criteria"] = exclusion_criteria
            data["modality"] = modality

            # Write back to config.json
            with open(json_path, 'w', encoding='utf-8') as file:
                json.dump(data, file, indent=4)
                
            file_saved = True
            file_status_msg = f"Successfully updated config at {json_path}"
            
        except Exception as e:
            # We catch the error but DO NOT return yet, so the broadcast still fires
            file_status_msg = f"Failed to save to disk: {str(e)}"

    # 2. ALWAYS Broadcast to the Frontend
    try:
        async with httpx.AsyncClient() as broadcast_client:
            await broadcast_client.post(
                "http://localhost:8150/ws-broadcast", 
                json={
                    "type": "population_output",
                    "payload": {
                        "tool": "write_inclusion_criteria",
                        "status": "completed",
                        "inclusion_criteria": inclusion_criteria,
                        "exclusion_criteria": exclusion_criteria,
                        "modality": modality
                    },
                    "session_id": session_id
                }
            )
            
        return {
            "status": "success", 
            "message": f"Broadcast successful. {file_status_msg}",
            "saved_path": str(json_path) if file_saved else "N/A"
        }
        
    except Exception as e:
        # If the broadcast itself fails, then we report a hard error
        return {
            "status": "error", 
            "message": f"Broadcast failed: {str(e)}. File status: {file_status_msg}"
        }