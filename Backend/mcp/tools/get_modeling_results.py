from . import mcp 
from pathlib import Path
import pandas as pd
from typing import Annotated
from pydantic import Field

import pandas as pd
from pathlib import Path
from typing import Annotated, Literal
from pydantic import Field

@mcp.tool()
def get_modeling_results(
) -> str:
    """
    Retrieves the latest machine learning modeling artifacts from the local file system. this will git you all the required data with one request

    Use this tool in scenarios such as:
    1. When you need to retrieve the modeling and the feature extraction results to write your report
    """
    mode = "all"
    try:
        # 1. Resolve Paths
        current_dir = Path(__file__).resolve().parent
        logging_path = current_dir.parent.parent / "agent_system" / "workspace"

        # Map modes to specific files (or all of them)
        all_files = {
            "features": logging_path / "features" / "top_10_features.csv",
            # "tuning": logging_path / "model_1_results.csv",
            "evaluation": logging_path / "models" / "model_1" / "independent_eval_results_model_1.csv",
            "comparisons": logging_path / "models" / "model_comparison_results.csv",
            "summary": logging_path / "models" / "model_summary.csv"
        }

        # Select which files to read based on the 'mode' argument
        files_to_read = all_files if mode == "all" else {mode: all_files[mode]}
        
        results = []

        # 2. Check Directory
        if not logging_path.exists():
            return f"Error: The directory '{logging_path}' does not exist."

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

        return "\n\n".join(results)

    except Exception as e:
        return f"Critical error in get_modeling_results: {str(e)}"


# current_dir = Path(__file__).resolve().parent
# logging_path = current_dir.parent.parent / "agent_system" / "memory_output_logs"

# data = pd.read_csv(logging_path / "independent_eval_results_tuned_model_1.csv")
# print(data)