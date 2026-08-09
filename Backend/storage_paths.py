"""Canonical paths for all generated backend data.

Application code should keep using logical workspace names (``masks``,
``reports``, ``state.json`` and so on) and resolve them through
``workspace_subpath``.  This keeps persisted/API paths stable while the
physical layout stays categorized under ``Backend/storage``.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Final

_BACKEND_DIR = Path(__file__).resolve().parent


def _default_storage_root() -> Path:
    container_root = Path("/storage")
    if container_root.exists():
        return container_root
    return _BACKEND_DIR / "storage"


def _resolve_configured_path(value: str | os.PathLike[str]) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _BACKEND_DIR / path
    return path.resolve()


BACKEND_STORAGE_ROOT: Final[Path] = _resolve_configured_path(
    os.environ.get("BACKEND_STORAGE_ROOT", _default_storage_root())
)

HYPOTHESES_DIR: Final[Path] = _resolve_configured_path(
    os.environ.get("AGENTS_WORKSPACE_DIR", BACKEND_STORAGE_ROOT / "hypotheses")
)
MODELS_DIR: Final[Path] = _resolve_configured_path(
    os.environ.get("MODELS_DIR", BACKEND_STORAGE_ROOT / "models")
)
MEDICAL_DIR: Final[Path] = _resolve_configured_path(
    os.environ.get("GENERAL_DATA_DIR", BACKEND_STORAGE_ROOT / "data" / "medical")
)
MEDICAL_IMAGES_DIR: Final[Path] = MEDICAL_DIR / "images"
MEDICAL_LABELS_DIR: Final[Path] = MEDICAL_DIR / "labels"
MEDICAL_EXPORTS_DIR: Final[Path] = MEDICAL_DIR / "exports"
NNUNET_DIR: Final[Path] = _resolve_configured_path(
    os.environ.get("NNUNET_DIR", BACKEND_STORAGE_ROOT / "data" / "nnunet")
)
JOBS_DIR: Final[Path] = _resolve_configured_path(
    os.environ.get("INFERENCE_JOB_DIR", BACKEND_STORAGE_ROOT / "jobs" / "inference")
)
CACHE_DIR: Final[Path] = BACKEND_STORAGE_ROOT / "cache"
LOGS_DIR: Final[Path] = _resolve_configured_path(
    os.environ.get("BACKEND_LOG_DIR", BACKEND_STORAGE_ROOT / "logs")
)
TEMP_DIR: Final[Path] = _resolve_configured_path(
    os.environ.get("BACKEND_TEMP_DIR", BACKEND_STORAGE_ROOT / "temp")
)

_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_STATE_FILES = {
    "state.json",
    "scratchpad.json",
    "config.json",
    "segmentation_manifest.json",
    "review_draft.json",
    "review_result.json",
    "literature_context.json",
}
_WORKSPACE_DIR_MAP = {
    "populations": ("data", "populations"),
    "volumes": ("data", "volumes"),
    "masks": ("data", "masks"),
    "outputs": ("outputs", "agents"),
    "literature_review_output": ("outputs", "literature"),
    "overlays": ("outputs", "overlays"),
    "features": ("outputs", "features"),
    "modeling": ("outputs", "modeling"),
    "models": ("outputs", "modeling"),
    "reports": ("outputs", "reports"),
    ".knowledge_cache": ("cache", "literature"),
    "temp_segmentations": ("temp", "segmentations"),
}


def _safe_session_id(session_id: str) -> str:
    value = str(session_id).strip()
    if value.startswith("workspace_"):
        value = value[len("workspace_") :]
    if not value or not _SESSION_RE.fullmatch(value):
        raise ValueError(f"Invalid session id: {session_id!r}")
    return value


def workspace(session_id: str) -> Path:
    """Return the physical root for a session workspace."""
    return HYPOTHESES_DIR / f"workspace_{_safe_session_id(session_id)}"


def workspace_subpath(workspace_dir: str | os.PathLike[str], logical: str | os.PathLike[str]) -> Path:
    """Map a legacy logical workspace path into the categorized layout."""
    root = Path(workspace_dir).expanduser().resolve()
    raw = str(logical).replace("\\", "/").strip("/")
    if not raw or raw == ".":
        return root

    parts = PurePosixPath(raw).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Unsafe workspace path: {logical!r}")

    first, remainder = parts[0], parts[1:]
    if first in _STATE_FILES:
        mapped = ("state", first, *remainder)
    elif first in {"clinical_data.json", "clinical_data.csv"}:
        mapped = ("data", "populations", first, *remainder)
    else:
        mapped = (*_WORKSPACE_DIR_MAP.get(first, (first,)), *remainder)

    candidate = root.joinpath(*mapped).resolve()
    candidate.relative_to(root)
    return candidate


def ws_sub(session_id: str, logical: str | os.PathLike[str]) -> Path:
    """Resolve a logical path inside ``workspace_<session-id>``."""
    return workspace_subpath(workspace(session_id), logical)


def ensure_storage_layout() -> None:
    """Create the fixed runtime roots; session subfolders remain on-demand."""
    for path in (
        HYPOTHESES_DIR,
        MEDICAL_IMAGES_DIR,
        MEDICAL_LABELS_DIR,
        MEDICAL_EXPORTS_DIR,
        NNUNET_DIR / "nnUNet_raw",
        NNUNET_DIR / "nnUNet_preprocessed",
        NNUNET_DIR / "nnUNet_results",
        NNUNET_DIR / "mappings",
        MODELS_DIR,
        JOBS_DIR,
        CACHE_DIR / "huggingface",
        CACHE_DIR / "monailabel",
        CACHE_DIR / "literature",
        LOGS_DIR / "agent-system",
        LOGS_DIR / "radiology",
        LOGS_DIR / "nginx",
        TEMP_DIR / "radiology",
        TEMP_DIR / "vista",
        TEMP_DIR / "latex",
        BACKEND_STORAGE_ROOT / "_migrated-legacy",
    ):
        path.mkdir(parents=True, exist_ok=True)

    runtime_vista_config = MODELS_DIR / "configs" / "vista"
    runtime_vista_config.mkdir(parents=True, exist_ok=True)
    configured_source = os.environ.get("VISTA_CONFIG_SOURCE_DIR")
    source_candidates = [
        Path(configured_source) if configured_source else None,
        _BACKEND_DIR / "models" / "configs" / "vista",
        _BACKEND_DIR / "apps" / "radiology" / "models" / "configs" / "vista",
    ]
    for source_dir in source_candidates:
        if source_dir is None or not source_dir.is_dir():
            continue
        for source_file in source_dir.glob("*.json"):
            destination = runtime_vista_config / source_file.name
            if not destination.exists():
                shutil.copy2(source_file, destination)
        break


__all__ = [
    "BACKEND_STORAGE_ROOT",
    "HYPOTHESES_DIR",
    "MODELS_DIR",
    "MEDICAL_DIR",
    "MEDICAL_IMAGES_DIR",
    "MEDICAL_LABELS_DIR",
    "MEDICAL_EXPORTS_DIR",
    "NNUNET_DIR",
    "JOBS_DIR",
    "CACHE_DIR",
    "LOGS_DIR",
    "TEMP_DIR",
    "workspace",
    "workspace_subpath",
    "ws_sub",
    "ensure_storage_layout",
]
