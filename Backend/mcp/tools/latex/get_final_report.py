import os
import json
from pathlib import Path
from typing import Annotated
from pydantic import Field
from .. import mcp
from storage_paths import HYPOTHESES_DIR, workspace_subpath

#TODO the path of the generated report is fixed, this needs to be dynamic

@mcp.tool()
async def get_final_report(
    report_filename: Annotated[str, Field(description="The name of the JSON file to read from the workspace. Defaults to 'final_report.json' if not specified.")] = "final_report.json"
) -> str:
    """
    Retrieves the content of a specific report file from the memory workspace.

    Use this tool in scenarios such as:
    1. When you need to read the 'final_report.json' to start converting it to latex.
    """

    # Go one level up (parent folder), then into memory/workspace/
    workspace_dir = Path(os.environ.get("WORKSPACE_DIR", str(HYPOTHESES_DIR / "workspace_default")))
    file_path = workspace_subpath(workspace_dir, f"reports/{report_filename}")

    if not file_path.exists():
        # Returning a clear error string is often better than raising an Exception 
        # for agents, as it allows them to self-correct.
        return f"Error: The file '{report_filename}' was not found at {file_path}."

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # Return pretty-printed string for the agent to read easily
        return json.dumps(data, indent=2, ensure_ascii=False)
        
    except json.JSONDecodeError:
        return f"Error: The file '{report_filename}' is not a valid JSON file."
    except Exception as e:
        return f"Error reading file: {str(e)}"
    
# if __name__ == "__main__":
#     import asyncio
#     import sys
    
#     # 1. Force the standard output to use UTF-8
#     sys.stdout.reconfigure(encoding='utf-8')

#     # 2. Run the async function properly
#     try:
#         print(asyncio.run(get_final_report()))
#     except Exception as e:
#         print(f"An error occurred: {e}")
