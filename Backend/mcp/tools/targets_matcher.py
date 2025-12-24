import os
import csv
import numpy as np
from typing import List
from pathlib import Path
from . import mcp
from tool_logging import ToolEvent, log_tool_state 

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
    root_dir = Path(__file__).parent.parent.parent
    output_dir = os.path.join(root_dir, 'agent_system', 'workspace', 'populations', 'targets')
    
    os.makedirs(output_dir, exist_ok=True)

    csv_path = os.path.join(output_dir, "targets.csv")
    targets = np.random.randint(0, 2, size=len(datasetlist))

    with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "target"])
        for uid, target in zip(datasetlist, targets):
            writer.writerow([uid, int(target)])

    finished_event = ToolEvent('tool_finished', 'study_population_planner_agent', {'tool_name': 'generate_target', 'progress': 'done'})
    log_tool_state(finished_event)

    return csv_path
