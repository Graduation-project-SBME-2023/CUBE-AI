from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

# ``TaskXXX`` is retained for datasets created by the project's legacy
# endpoint; new nnUNet v2 datasets use the canonical ``DatasetXXX`` prefix.
_DATASET_NAME = re.compile(r"^(?:Dataset|Task)(?P<id>\d{3})_(?P<name>.+)$")


@dataclass(frozen=True)
class NNUNetPaths:
    raw: Path
    preprocessed: Path
    results: Path


def configure_nnunet_environment(root: str | os.PathLike[str]) -> NNUNetPaths:
    """Configure nnUNet's required directory environment consistently."""
    root_path = Path(root).expanduser().resolve()
    paths = NNUNetPaths(
        raw=root_path / "nnUNet_raw",
        preprocessed=root_path / "nnUNet_preprocessed",
        results=root_path / "nnUNet_results",
    )
    os.environ["nnUNet_raw"] = str(paths.raw)
    os.environ["nnUNet_preprocessed"] = str(paths.preprocessed)
    os.environ["nnUNet_results"] = str(paths.results)
    return paths


def resolve_dataset(raw_dir: Path, model_name: str) -> tuple[str, str]:
    """Return a deterministic nnUNet dataset name and its three-digit ID."""
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"nnUNet_raw directory not found: {raw_dir}")

    matches: list[tuple[str, str]] = []
    suffix = f"_{model_name}"
    for candidate in sorted(raw_dir.iterdir(), key=lambda path: path.name.lower()):
        if not candidate.is_dir() or not candidate.name.endswith(suffix):
            continue
        parsed = _DATASET_NAME.fullmatch(candidate.name)
        if parsed:
            matches.append((candidate.name, parsed.group("id")))

    if not matches:
        raise ValueError(
            f"No DatasetXXX_{model_name} directory exists in {raw_dir}"
        )
    if len(matches) > 1:
        names = ", ".join(name for name, _ in matches)
        raise ValueError(f"Multiple nnUNet datasets match {model_name}: {names}")
    return matches[0]
