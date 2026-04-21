import json
import httpx
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
    Updates or creates a JSON file with inclusion and exclusion criteria and modalities for a population.
    Use this tool when you need to save or update the clinical rules that define which patients 
    belong to the target cohort.
    """
    # 1. Resolve paths dynamically based on the nested location
    current_file_path = Path(__file__).resolve()
    backend_dir = current_file_path.parent.parent.parent.parent 
    
    json_path = backend_dir / "agent_system" / "workspace" / "populations" / "populations" / "population.json"
    
    # 2. Read existing data if the file exists
    data = {}
    if json_path.exists():
        try:
            with open(json_path, 'r', encoding='utf-8') as file:
                data = json.load(file)
        except json.JSONDecodeError:
            data = {} 
    else:
        json_path.parent.mkdir(parents=True, exist_ok=True)

    # 3. Update the data
    data["inclusion_criteria"] = inclusion_criteria
    data["exclusion_criteria"] = exclusion_criteria
    data["modality"] = modality

    # 4. Write back to the file
    try:
        with open(json_path, 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=4)
            
        # ============================================================
        # SIDE-CHANNEL: Broadcast the criteria directly to the Frontend
        # ============================================================
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
                    }
                }
            )

        return {
            "status": "success", 
            "message": f"Successfully updated inclusion and exclusion criteria in {json_path.name}",
            "saved_path": str(json_path)
        }
    except Exception as e:
        return {
            "status": "error", 
            "message": f"Failed to write to file or broadcast: {str(e)}"
        }