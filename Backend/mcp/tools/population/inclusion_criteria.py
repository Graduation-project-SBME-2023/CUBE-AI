import json
from pydantic import Field
from pathlib import Path
from typing import Annotated
from .. import mcp

@mcp.tool()
async def write_inclusion_criteria(
    criteria: Annotated[str, Field(description="The inclusion criteria text to define the population.")],
    modality: Annotated[str, Field(description="The imaging modality associated with these criteria (e.g., 'CT', 'MRI').")]
) -> dict:
    """
    Updates or creates a JSON file with inclusion criteria and modalities for a population.
    Use this tool when you need to save or update the rules that define which patients 
    or volumes belong to a specific target population.
    """
    # 1. Resolve paths dynamically based on the new nested location
    # Current file: Backend/mcp/tools/population/inclusion_criteria.py
    # Target workspace: Backend/workspace/populations/populations/population.json
    current_file_path = Path(__file__).resolve()
    
    # Go up 4 levels: population -> tools -> mcp -> Backend
    backend_dir = current_file_path.parent.parent.parent.parent 
    
    json_path = backend_dir / "agent_system" / "workspace" / "populations" / "populations" / "population.json"
    
    # 2. Read existing data if the file exists
    data = {}
    if json_path.exists():
        try:
            with open(json_path, 'r', encoding='utf-8') as file:
                data = json.load(file)
        except json.JSONDecodeError:
            # File exists but is empty or corrupt; start fresh
            data = {} 
    else:
        # Create the necessary directories if they don't exist
        json_path.parent.mkdir(parents=True, exist_ok=True)

    # 3. Update the data
    data["inclusion_criteria"] = criteria
    data["modality"] = modality

    # 4. Write back to the file
    try:
        with open(json_path, 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=4)
        return {
            "status": "success", 
            "message": f"Successfully updated inclusion criteria in {json_path.name}",
            "saved_path": str(json_path)
        }
    except Exception as e:
        return {
            "status": "error", 
            "message": f"Failed to write to file: {str(e)}"
        }