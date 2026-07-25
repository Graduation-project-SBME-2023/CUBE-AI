import os
from pathlib import Path

from typing import Annotated, Optional
from pydantic import BeforeValidator

WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "../../../agent_system/workspace")
os.makedirs(WORKSPACE_DIR, exist_ok=True)


def get_workspace() -> str:
    return WORKSPACE_DIR


def base_on_workspace_dir(path: Optional[str]) -> Optional[str]:
    if path is None:
        return None
    if not isinstance(path, (str, Path)) or not str(path).strip():
        return None

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


WorkspacePath = Annotated[Optional[str], BeforeValidator(base_on_workspace_dir)]