"""``browse_workspace``: list the files of one directory of the session workspace."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .._toolkit import error_result, tool
from ..common.manifests import READ_ONLY
from ..common.workspace import WorkspacePath, workspace_dir


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
async def browse_workspace(params: BrowseWorkspaceInput) -> str | dict:
    """Browse files in a workspace directory, filtered by file extension.

    The directory is workspace-relative (absolute paths are re-rooted in this
    session's workspace). Returns the matching files with their sizes.
    """
    extension = params.file_type.strip()
    if extension and not extension.startswith("."):
        extension = f".{extension}"

    # Resolved (L9): "..", symlinks and the drive letter compare like for like
    # with the workspace root below.
    directory = Path(params.directory).resolve()
    root = workspace_dir().resolve()
    if not directory.exists():
        return error_result("not_found", f"Directory not found: {_relative(directory, root)}")
    if not directory.is_dir():
        return error_result("invalid_args", f"Path is not a directory: {_relative(directory, root)}")

    pattern = f"**/*{extension}" if params.recursive else f"*{extension}"
    files = [
        {"path": _relative(path, root), "size_kb": round(path.stat().st_size / 1024, 1)}
        for path in sorted(directory.glob(pattern))
    ]
    return json.dumps({
        "status": "ok",
        "directory": _relative(directory, root),
        "file_type": extension,
        "recursive": params.recursive,
        "count": len(files),
        "files": files,
    }, indent=2)
