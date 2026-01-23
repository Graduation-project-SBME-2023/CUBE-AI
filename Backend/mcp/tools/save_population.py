from pathlib import Path
from datetime import datetime
from . import mcp
import json

@mcp.tool()
async def save_final_output(data: dict, workspace_dir: str):
    """
    Save the agent's final output to a JSON file.
    
    Arguments:
        data -- A Python dict containing the agent's final output.
        workspace_dir -- The workspace directory to save the final output to.
    
    Returns:
        A confirmation message with the saved file path.
    """
        
    # current_dir = Path(__file__).resolve().parent
    # json_path = current_dir.parent.parent / "agent_system" / "workspace" / "config.json"

    # data['hypothesis'] = 'The radiomics features can detect weather the spleen is cancerous is not.'

    json_path = Path(workspace_dir) / "config.json"
    
    # Ensure it's serializable
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    return {"status": "success", "file": json_path}