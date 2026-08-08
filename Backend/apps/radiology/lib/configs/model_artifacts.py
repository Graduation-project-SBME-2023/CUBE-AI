"""Shared integrity checks for downloadable model checkpoints."""

import json
import os
import zipfile
from typing import Callable, List, Optional


NNINTERACTIVE_REPO = "nnInteractive/nnInteractive"
NNINTERACTIVE_REQUIRED_FILES = (
    "nnInteractive_v1.0/dataset.json",
    "nnInteractive_v1.0/inference_session_class.json",
    "nnInteractive_v1.0/LICENSE",
    "nnInteractive_v1.0/plans.json",
    "nnInteractive_v1.0/fold_0/checkpoint_final.pth",
)


def is_complete_model_archive(path: str) -> bool:
    """Validate the ZIP central directory used by modern PyTorch/NumPy files."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            return False
        if not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path, "r") as archive:
            return bool(archive.infolist())
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return False


def ensure_model_archive(
    path: str,
    download: Callable[[], object],
    *,
    description: str,
    log: Callable[[str], None] = print,
) -> str:
    """Download a checkpoint when it is missing or structurally incomplete."""
    if is_complete_model_archive(path):
        log(f"{description} is complete at {path}")
        return path

    if os.path.isfile(path):
        log(f"Removing incomplete {description} before retry: {path}")
        os.remove(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    log(f"Downloading {description} to {path}")
    download()

    if not is_complete_model_archive(path):
        raise RuntimeError(f"{description} download is incomplete or invalid at {path}")
    log(f"{description} download verified at {path}")
    return path


def _valid_json(path: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return bool(json.load(stream))
    except (OSError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def find_incomplete_nninteractive_files(models_dir: str) -> List[str]:
    incomplete = []
    for relative_path in NNINTERACTIVE_REQUIRED_FILES:
        path = os.path.join(models_dir, *relative_path.split("/"))
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            incomplete.append(relative_path)
        elif relative_path.endswith(".json") and not _valid_json(path):
            incomplete.append(relative_path)
        elif relative_path.endswith(".pth") and not is_complete_model_archive(path):
            incomplete.append(relative_path)
    return incomplete


def ensure_nninteractive_artifacts(
    models_dir: str,
    *,
    downloader: Optional[Callable[..., str]] = None,
    log: Callable[[str], None] = print,
) -> str:
    """Ensure every file in the nnInteractive v1.0 snapshot is complete."""
    incomplete = find_incomplete_nninteractive_files(models_dir)
    if not incomplete:
        log(f"nnInteractive model is complete at {models_dir}")
        return models_dir

    log("nnInteractive model is incomplete; downloading/resuming: " + ", ".join(incomplete))
    os.makedirs(models_dir, exist_ok=True)
    for relative_path in incomplete:
        invalid_path = os.path.join(models_dir, *relative_path.split("/"))
        if os.path.isfile(invalid_path):
            log(f"Removing incomplete nnInteractive file before retry: {invalid_path}")
            os.remove(invalid_path)

    if downloader is None:
        from huggingface_hub import snapshot_download

        downloader = snapshot_download
    downloader(
        repo_id=NNINTERACTIVE_REPO,
        allow_patterns=["nnInteractive_v1.0/*"],
        local_dir=models_dir,
    )

    incomplete = find_incomplete_nninteractive_files(models_dir)
    if incomplete:
        raise RuntimeError(
            f"nnInteractive download is incomplete at {models_dir}. "
            "Missing or invalid files: " + ", ".join(incomplete)
        )
    log(f"nnInteractive model download verified at {models_dir}")
    return models_dir
