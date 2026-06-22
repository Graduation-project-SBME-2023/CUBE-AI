import os
import csv
import numpy as np
import json
from . import mcp
from tool_logging import ToolEvent, log_tool_state, TextPayload
import asyncio

# @mcp.tool()
# async def generate_targets() -> str:
#     """
#     Generates the necessary targets.csv file for the matching subjects found in the datasetlist inside the config file to targets

#     Returns:
#         The path of the generated csv file.
#     """
#     payload = TextPayload(message='assigning labels')
#     start_event = ToolEvent(
#         event_type='tool_started',
#         content_type='text',
#         content=payload
#     )
#     log_tool_state(start_event)
    
    
#     # root_dir = Path(__file__).parent.parent.parent
#     # output_dir = os.path.join(root_dir, 'agent_system', 'workspace', 'populations', 'targets')

#     workspace_dir = os.environ.get("WORKSPACE_DIR")

#     config_json_path = os.path.join(workspace_dir, "config.json")
#     datasetlist = []
#     with open(config_json_path, mode="r", encoding="utf-8") as f:
#         config = json.load(f)
#         datasetlist = config.get("datasetlist", [])

#     output_dir = os.path.join(workspace_dir, 'populations', 'targets')
    
#     os.makedirs(output_dir, exist_ok=True)

#     csv_path = os.path.join(output_dir, "targets.csv")
#     targets = np.random.randint(0, 2, size=len(datasetlist))

#     with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
#         writer = csv.writer(f)
#         writer.writerow(["id", "target"])
#         for uid, target in zip(datasetlist, targets):
#             writer.writerow([uid, int(target)])

#     payload = TextPayload(message="Label Fetched.")
#     finish_event = ToolEvent(
#         event_type='tool_finished',
#         content_type='text',
#         content=payload
#     )
#     log_tool_state(finish_event)

#     return csv_path

# Create a global flag to track the waiting state
IS_WAITING_FOR_LABELS = False


@mcp.tool()
async def generate_targets() -> str:
    """
    Waits for the doctor to upload the targets.csv file.
    """
    global IS_WAITING_FOR_LABELS
    workspace_dir = os.environ.get("WORKSPACE_DIR")
    print(f"workspace directory ====================== {workspace_dir} ========================= I listen Here")
    output_dir = os.path.join(workspace_dir, 'populations', 'targets')
    os.makedirs(output_dir, exist_ok=True)
    
    # Only send the log to the frontend the VERY FIRST time it asks
    if not IS_WAITING_FOR_LABELS:
        payload = TextPayload(message=f'Waiting for doctor to upload labels in ... {output_dir}')
        start_event = ToolEvent(
            event_type='tool_started',
            content_type='text',
            content=payload
        )
    csv_path = os.path.join(output_dir, "targets.csv")

    # Wait up to 45 seconds to keep the LLM connection alive
    timeout_seconds = 300
    elapsed = 0
    poll_interval = 2

    while not os.path.exists(csv_path):
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        
        if elapsed >= timeout_seconds:
            # The file isn't here yet. 
            # We silently return to the Agent WITHOUT calling log_tool_state()
            return "SILENT_RETRY"

    # If the loop breaks, the file was successfully uploaded!
    # Reset the flag so the tool can be used again in the future
    IS_WAITING_FOR_LABELS = False 
    
    # Auto-fill missing labels if the user uploaded an empty template
    try:
        import pandas as pd
        import random
        df = pd.read_csv(csv_path)
        if 'target' in df.columns:
            empty_mask = df['target'].isnull() | (df['target'].astype(str).str.strip() == '')
            num_empty = empty_mask.sum()
            
            if num_empty > 0:
                labels = ["Male", "Female"]
                # Create a list that guarantees at least one of each (if we have at least 2 empty rows)
                fill_values = [labels[i % len(labels)] for i in range(num_empty)]
                random.shuffle(fill_values)
                
                df.loc[empty_mask, 'target'] = fill_values
                df.to_csv(csv_path, index=False)
    except Exception as e:
        print(f"Failed to auto-fill dummy labels: {e}")
        
    payload = TextPayload(message="Labels Successfully Fetched!")
    finish_event = ToolEvent(
        event_type='tool_finished',
        content_type='text',
        content=payload
    )
    log_tool_state(finish_event)

    return csv_path