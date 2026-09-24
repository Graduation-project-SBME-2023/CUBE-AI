"""Workspace artifact file serving.

app.py must include this router before it mounts StaticFiles at /files;
the mount would otherwise shadow the legacy workspace-file route.
"""

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from storage_paths import workspace, workspace_subpath
from api.runtime import agents_workspace_dir

router = APIRouter()


@router.get("/files/{workspace_name}/{logical_path:path}", name="legacy_workspace_file")
def serve_workspace_file(workspace_name: str, logical_path: str):
    """Serve legacy logical workspace URLs from the categorized layout."""
    if not workspace_name.startswith("workspace_"):
        raise HTTPException(status_code=404, detail="Workspace not found")

    first = logical_path.replace("\\", "/").split("/", 1)[0]
    allowed = {
        "reports", "overlays", "features", "modeling", "outputs",
        "populations", "volumes", "masks", "data",
    }
    if first not in allowed:
        raise HTTPException(status_code=404, detail="Artifact category not found")

    workspace_root = workspace(workspace_name)
    normalized = logical_path.replace("\\", "/").strip("/")
    physical_prefixes = (
        "outputs/agents/", "outputs/literature/", "outputs/overlays/",
        "outputs/features/", "outputs/modeling/", "outputs/reports/",
        "data/populations/", "data/volumes/", "data/masks/",
    )
    if normalized.startswith(physical_prefixes):
        target = (workspace_root / normalized).resolve()
    else:
        try:
            target = workspace_subpath(workspace_root, normalized)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="Invalid artifact path") from exc

    try:
        target.relative_to(workspace_root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Artifact path escapes workspace") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(target)


@router.get("/static/{file_path:path}")
def serve_static_pdf(file_path: str):
    # Resolve the requested path
    full_path = os.path.normpath(file_path)
    if not os.path.isabs(full_path):
        full_path = os.path.abspath(full_path)

    # Security: ensure the resolved path is strictly inside agents_workspace_dir.
    # This prevents path traversal attacks (e.g., /static/../../../etc/passwd).
    allowed_root = os.path.realpath(agents_workspace_dir)
    resolved = os.path.realpath(full_path)
    if not resolved.startswith(allowed_root + os.sep) and resolved != allowed_root:
        raise HTTPException(
            status_code=403,
            detail="Access denied: path is outside the allowed workspace directory."
        )

    if not os.path.exists(resolved):
        raise HTTPException(status_code=404, detail=f"File not found: {resolved}")

    if resolved.endswith(".pdf"):
        return FileResponse(
            path=resolved,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"inline; filename={os.path.basename(resolved)}"
            },
            content_disposition_type="inline",
        )

    # detect GIF explicitly
    if resolved.endswith(".gif"):
        return FileResponse(path=resolved, media_type="image/gif")

    return FileResponse(path=resolved)
