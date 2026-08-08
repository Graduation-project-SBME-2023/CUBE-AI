"""Download and validate all VoxTell model artifacts used by the app."""

import json
import os
import zipfile
from typing import Callable, List, Optional


VOXTELL_REPO = "mrokuss/VoxTell"
VOXTELL_REQUIRED_FILES = (
    "voxtell_v1.1/plans.json",
    "voxtell_v1.1/fold_0/INFO.txt",
    "voxtell_v1.1/fold_0/checkpoint_final.pth",
    "voxtell_v1.0/plans.json",
    "embeddings/voxtell_v1.1/labels.json",
    "embeddings/voxtell_v1.1/text_embeddings.npz",
)


def _valid_json(path: str, expected_type: type) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            value = json.load(stream)
        return isinstance(value, expected_type) and bool(value)
    except (OSError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def _valid_archive(path: str) -> bool:
    """Check ZIP central-directory integrity without reading multi-GB tensor data."""
    try:
        if not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path, "r") as archive:
            return bool(archive.infolist())
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return False


def find_incomplete_voxtell_files(models_dir: str) -> List[str]:
    """Return required VoxTell files that are missing, empty, or invalid."""
    incomplete = []
    for relative_path in VOXTELL_REQUIRED_FILES:
        path = os.path.join(models_dir, *relative_path.split("/"))
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            incomplete.append(relative_path)
        elif relative_path.endswith("plans.json") and not _valid_json(path, dict):
            incomplete.append(relative_path)
        elif relative_path.endswith("labels.json") and not _valid_json(path, list):
            incomplete.append(relative_path)
        elif relative_path.endswith((".pth", ".npz")) and not _valid_archive(path):
            incomplete.append(relative_path)
    return incomplete


def ensure_voxtell_artifacts(
    models_dir: str,
    *,
    downloader: Optional[Callable[..., str]] = None,
    log: Callable[[str], None] = print,
) -> str:
    """Ensure v1.0, v1.1, and precomputed embeddings are fully downloaded."""
    incomplete = find_incomplete_voxtell_files(models_dir)
    if not incomplete:
        log(f"VoxTell model artifacts are complete at {models_dir}")
        return models_dir

    log(
        "VoxTell model artifacts are incomplete; downloading/resuming files: "
        + ", ".join(incomplete)
    )
    os.makedirs(models_dir, exist_ok=True)

    # Remove only files proven invalid so stale Hugging Face metadata cannot
    # cause a truncated destination to be reused.
    for relative_path in incomplete:
        invalid_path = os.path.join(models_dir, *relative_path.split("/"))
        if os.path.isfile(invalid_path):
            log(f"Removing incomplete VoxTell file before retry: {invalid_path}")
            os.remove(invalid_path)

    if downloader is None:
        from huggingface_hub import snapshot_download

        downloader = snapshot_download

    downloader(
        repo_id=VOXTELL_REPO,
        allow_patterns=[
            "voxtell_v1.0/*",
            "voxtell_v1.1/*",
            "embeddings/*",
        ],
        local_dir=models_dir,
    )

    incomplete = find_incomplete_voxtell_files(models_dir)
    if incomplete:
        raise RuntimeError(
            f"VoxTell artifact download is incomplete at {models_dir}. "
            "Missing or invalid files: " + ", ".join(incomplete)
        )

    log(f"VoxTell model artifact download verified at {models_dir}")
    return models_dir
