from . import mcp 
from pathlib import Path
import os
import pandas as pd
import sys
from pathlib import Path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))
from tool_logging import ToolEvent, log_tool_state, TextPayload
from storage_paths import HYPOTHESES_DIR, workspace_subpath


@mcp.tool()
def get_modeling_results() -> str:
    """
    Retrieves the latest machine learning modeling artifacts from the local file system. this will git you all the required data with one request

    Use this tool in scenarios such as:
    1. When you need to retrieve the modeling and the feature extraction results to write your report
    """
    mode = "all"
    WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR")
    try:
        payload = TextPayload(message='fetching results')
        start_event = ToolEvent(
            event_type='tool_started',
            content_type='text',
            content=payload
        )
        log_tool_state(start_event)
        # 1. Resolve Paths
        if not WORKSPACE_DIR:
            WORKSPACE_DIR = str(HYPOTHESES_DIR / "workspace_default")
        WORKSPACE_DIR = Path(WORKSPACE_DIR)

        # current_dir = Path(__file__).resolve().parent
        # logging_path = current_dir.parent.parent / "agent_system" / "workspace"

        # Map modes to specific files (or all of them)
        all_files = {
            "features": workspace_subpath(WORKSPACE_DIR, "features/top_10_features.csv"),
            # "tuning": logging_path / "model_1_results.csv",
            "evaluation": workspace_subpath(WORKSPACE_DIR, "models/models/model_1/independent_eval_results_model_1.csv"),
            "comparisons": workspace_subpath(WORKSPACE_DIR, "models/model_comparison_results.csv"),
            "summary": workspace_subpath(WORKSPACE_DIR, "models/model_summary.csv")
        }

        # Select which files to read based on the 'mode' argument
        files_to_read = all_files if mode == "all" else {mode: all_files[mode]}
        
        results = []

        # 2. Check Directory
        if not WORKSPACE_DIR.exists():
            return f"Error: The directory '{WORKSPACE_DIR}' does not exist."

        # 3. Read Files with Error Handling
        for key, file_path in files_to_read.items():
            header = key.replace("_", " ").title()
            
            if not file_path.exists():
                results.append(f"### {header}\n*Error: File not found at {file_path}*")
                continue

            try:
                df = pd.read_csv(file_path)
                if df.empty:
                    results.append(f"### {header}\n*Warning: File is empty.*")
                else:
                    # Convert to markdown for clear AI reading
                    results.append(f"### {header}\n{df.to_markdown(index=False)}")
            except Exception as e:
                results.append(f"### {header}\n*Error reading CSV: {str(e)}*")
        payload = TextPayload(message="study data results Fetched")
        finish_event = ToolEvent(
            event_type='tool_finished',
            content_type='text',
            content=payload
        )
        log_tool_state(finish_event)
        return "\n\n".join(results)

    except Exception as e:
        return f"Critical error in get_modeling_results: {str(e)}"


# current_dir = Path(__file__).resolve().parent
# logging_path = current_dir.parent.parent / "agent_system" / "memory_output_logs"

# data = pd.read_csv(logging_path / "independent_eval_results_tuned_model_1.csv")
# print(data)
