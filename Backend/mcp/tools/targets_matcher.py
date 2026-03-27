import os
import csv
import numpy as np
from typing import List
from pathlib import Path
from . import mcp
from tool_logging import ToolEvent, log_tool_state, TextPayload

@mcp.tool()
async def generate_targets(
    datasetlist: List[str],
) -> str:
    """
    Generates the necessary targets.csv file for the matching subjects to targets

    Args:
        datasetlist: The complete list of the volume_instance_uid's included in the population.

    Returns:
        The path of the generated csv file.
    """
    payload = TextPayload(message='assigning labels')
    start_event = ToolEvent(
        event_type='tool_started',
        content_type='text',
        content=payload
    )
    log_tool_state(start_event)
    
    
    # root_dir = Path(__file__).parent.parent.parent
    # output_dir = os.path.join(root_dir, 'agent_system', 'workspace', 'populations', 'targets')

    workspace_dir = os.environ.get("WORKSPACE_DIR")

    output_dir = os.path.join(workspace_dir, 'populations', 'targets', 'targets.csv')
    
    os.makedirs(output_dir, exist_ok=True)

    csv_path = os.path.join(output_dir, "targets.csv")
    targets = np.random.randint(0, 2, size=len(datasetlist))

    with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "target"])
        for uid, target in zip(datasetlist, targets):
            writer.writerow([uid, int(target)])

    payload = TextPayload(message="Fetching labels...")
    finish_event = ToolEvent(
        event_type='tool_finished',
        content_type='text',
        content=payload
    )
    log_tool_state(finish_event)

    return csv_path
