from .._toolkit import tool
from ..common.manifests import READ_ONLY
from pydantic import BaseModel, ConfigDict, Field
from ..common.workspace import WorkspacePath, workspace_dir
from pathlib import Path
import json

class BrowseWorkspaceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    directory: WorkspacePath = Field(
        default=".",  # the workspace root, resolved like any other path
        validate_default=True,
        description=(
            "Directory to browse. Can be absolute, relative, or workspace-relative "
            "(e.g. 'data/exports'). Defaults to the workspace root."
        ),
    )
    file_type: str = Field(
        default=".csv",
        description="File extension to filter by, with or without leading dot (e.g. '.csv' or 'csv').",
    )
    recursive: bool = Field(
        default=False,
        description="Whether to search subdirectories recursively. Defaults to False.",
    )


@tool(
    name="browse_workspace",
    visibility={"agent", "main"},
    idempotent=True,
    **READ_ONLY,
)
async def browse_workspace(params: BrowseWorkspaceInput) -> str:
    """Browse files in a workspace directory, filtered by file extension.

    Paths are resolved against the session workspace by the WorkspacePath validator.

    Args:
        params (BrowseWorkspaceInput):
            - directory (str): Workspace-relative or absolute path. Defaults to workspace root.
            - file_type (str): Extension to filter by (default '.csv').
            - recursive (bool): Search subdirectories recursively (default False).

    Returns:
        str: JSON list of matching files with sizes, or an error message.
    """
    # Normalise extension
    ext = params.file_type.strip()
    if ext and not ext.startswith("."):
        ext = f".{ext}"

    resolved = Path(params.directory)

    if not resolved.exists():
        return json.dumps({"status": "error", "message": f"Directory not found: {resolved}"}, indent=2)
    if not resolved.is_dir():
        return json.dumps({"status": "error", "message": f"Path is not a directory: {resolved}"}, indent=2)

    glob_pattern = f"**/*{ext}" if params.recursive else f"*{ext}"
    matches = sorted(resolved.glob(glob_pattern))

    workspace_root = workspace_dir()
    files = []
    for f in matches:
        try:
            rel = str(f.relative_to(workspace_root))
        except ValueError:
            rel = str(f)
        files.append({"path": rel, "size_kb": round(f.stat().st_size / 1024, 1)})

    return json.dumps({
        "status": "ok",
        "directory": str(resolved.relative_to(workspace_root)),
        "file_type": ext,
        "recursive": params.recursive,
        "count": len(files),
        "files": files,
    }, indent=2)
