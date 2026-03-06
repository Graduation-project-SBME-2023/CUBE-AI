import os
from pathlib import Path

from typing import Annotated
from pydantic import BeforeValidator

WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "../../../agent_system/workspace")


def get_workspace() -> str:
    return WORKSPACE_DIR


def base_on_workspace_dir(path: str) -> str:
    if not path or not path.strip():
        raise ValueError(f"Workspace dir must not be empty")

    p = Path(path)
    workspace_idx = next(
        (i for i, part in enumerate(p.parts) if part.startswith("workspace")),
        None
    )

    if workspace_idx is None:
        relative = p
    else:
        relative = Path(*p.parts[workspace_idx+1:])

    return str(Path(WORKSPACE_DIR) / relative)


WorkspacePath = Annotated[str, BeforeValidator(base_on_workspace_dir)]