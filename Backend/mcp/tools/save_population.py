from pathlib import Path
from datetime import datetime
from . import mcp
import json

@mcp.tool()
async def save_final_output(data: dict, filename: str = None):
    """
    Save the agent's final output to a JSON file.
    
    Arguments:
        data -- A Python dict containing the agent's final output.
        filename -- Optional filename. If not provided, a timestamped one will be created.
    
    Returns:
        A confirmation message with the saved file path.
    """
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        current_dir = Path(__file__).resolve().parent
        json_path = current_dir.parent / "memory" / "workspace" / "dataset.json"
        file_name = json_path      
        

    # Ensure it's serializable
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    return {"status": "success", "file": json_path}