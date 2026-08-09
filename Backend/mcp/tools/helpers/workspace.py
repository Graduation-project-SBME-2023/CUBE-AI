import os
from pathlib import Path

from typing import Annotated, Optional
from pydantic import BeforeValidator
from storage_paths import HYPOTHESES_DIR, workspace_subpath

WORKSPACE_DIR = os.environ.get(
    "WORKSPACE_DIR",
    str(HYPOTHESES_DIR / "workspace_default"),
)


def get_workspace() -> str:
    return WORKSPACE_DIR


def base_on_workspace_dir(path: Optional[str]) -> Optional[str]:
    if path is None:
        return None
    if not isinstance(path, (str, Path)) or not str(path).strip():
        return None

    root = Path(WORKSPACE_DIR).expanduser().resolve()
    p = Path(path).expanduser()
    if p.is_absolute():
        resolved = p.resolve()
        try:
            resolved.relative_to(root)
            return str(resolved)
        except ValueError:
            pass

    workspace_idx = next(
        (i for i, part in enumerate(p.parts) if part.startswith("workspace")),
        None
    )

    if workspace_idx is None:
        relative = p
    else:
        relative = Path(*p.parts[workspace_idx+1:])

    return str(workspace_subpath(root, relative))


WorkspacePath = Annotated[Optional[str], BeforeValidator(base_on_workspace_dir)]
