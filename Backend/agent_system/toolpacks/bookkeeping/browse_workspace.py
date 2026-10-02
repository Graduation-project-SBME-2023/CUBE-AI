from .._toolkit import error_result, tool
from ..common.manifests import READ_ONLY
from pydantic import BaseModel, ConfigDict, Field
from ..common.workspace import WorkspacePath, workspace_dir
from pathlib import Path
import json

def _relative(path: Path, root: Path) -> str:
    """``path`` relative to the workspace root, or as it is when it lies outside."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


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
    data_scopes={"phi_read"},
    timeout_s=30,
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

    # Resolved (L9): "..", symlinks and the drive letter compare like for like
    # with the workspace root below.
    resolved = Path(params.directory).resolve()
    workspace_root = workspace_dir().resolve()

    if not resolved.exists():
        return error_result("not_found", f"Directory not found: {_relative(resolved, workspace_root)}")
    if not resolved.is_dir():
        return error_result("invalid_args", f"Path is not a directory: {_relative(resolved, workspace_root)}")

    glob_pattern = f"**/*{ext}" if params.recursive else f"*{ext}"
    matches = sorted(resolved.glob(glob_pattern))

    files = [
        {"path": _relative(f, workspace_root), "size_kb": round(f.stat().st_size / 1024, 1)}
        for f in matches
    ]

    return json.dumps({
        "status": "ok",
        "directory": _relative(resolved, workspace_root),
        "file_type": ext,
        "recursive": params.recursive,
        "count": len(files),
        "files": files,
    }, indent=2)
