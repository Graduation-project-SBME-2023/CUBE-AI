"""The current call's session workspace (merged plan, Phase 2.2).

The workspace is the context's ``workspace_dir``, or the session's workspace
under ``storage_paths`` when the context names none. ``WorkspacePath`` resolves
a tool argument against it while dispatch validates the arguments (dispatch
sets the context first), so a path can never point into another session.
"""

from pathlib import Path

from typing import Annotated, Optional
from pydantic import BeforeValidator
from storage_paths import workspace, workspace_subpath

from .. import _toolkit


def workspace_dir() -> Path:
    """The session workspace of the current call. Raises ``ValueError``
    outside a dispatched call, which validation reports as a bad argument."""
    try:
        ctx = _toolkit.current_context()
    except _toolkit.NoToolContextError:
        raise ValueError("no tool context: the workspace is unknown") from None
    if ctx.workspace_dir is not None:
        return Path(ctx.workspace_dir)
    return workspace(ctx.session_id)


def get_workspace() -> str:
    return str(workspace_dir())


def base_on_workspace_dir(path: Optional[str]) -> Optional[str]:
    if path is None:
        return None
    if not isinstance(path, (str, Path)) or not str(path).strip():
        return None

    root = workspace_dir().expanduser().resolve()
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
