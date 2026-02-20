from pathlib import Path
from datetime import datetime
from . import mcp
import json
from tool_logging import ToolEvent, log_tool_state, TextPayload

@mcp.tool()
async def save_final_output(data: dict):
    """
    Save the agent's final output to a JSON file.
    
    Arguments:
        data -- A Python dict containing the agent's final output.
    
    Returns:
        A confirmation message with the saved file path.
    """
        
    current_dir = Path(__file__).resolve().parent
    json_path = current_dir.parent.parent / "agent_system" / "workspace" / "config.json"

    data['hypothesis'] = 'The radiomics features can detect whether the spleen is cancerous or not.'
    patient_count = len(data['patient_ids'])
    study_count = len(data['datasetlist'])

    payload = TextPayload(message=f'Selected {study_count} volumes from {patient_count} patients.')
    start_event = ToolEvent('tool_started', 'study_population_planner_agent', 'text', payload)
    log_tool_state(start_event)
    finished_event = ToolEvent('tool_finished', 'study_population_planner_agent', 'text', payload)
    log_tool_state(finished_event)
    
    # Ensure it's serializable
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    return {"status": "success", "file": json_path}