from __future__ import annotations

import os
from pathlib import Path

import pytest

from radiology_app.integrations.nnunet import (
    configure_nnunet_environment,
    resolve_dataset,
)


def test_configure_and_resolve_dataset(tmp_path: Path) -> None:
    paths = configure_nnunet_environment(tmp_path)
    (paths.raw / "Dataset200_brain_tumor").mkdir(parents=True)

    assert resolve_dataset(paths.raw, "brain_tumor") == (
        "Dataset200_brain_tumor",
        "200",
    )
    assert os.environ["nnUNet_results"] == str(paths.results)


def test_resolve_dataset_rejects_ambiguous_names(tmp_path: Path) -> None:
    raw = tmp_path / "nnUNet_raw"
    (raw / "Dataset200_brain_tumor").mkdir(parents=True)
    (raw / "Dataset201_brain_tumor").mkdir()

    with pytest.raises(ValueError, match="Multiple nnUNet datasets"):
        resolve_dataset(raw, "brain_tumor")


def test_resolve_dataset_supports_legacy_task_prefix(tmp_path: Path) -> None:
    raw = tmp_path / "nnUNet_raw"
    (raw / "Task042_legacy_model").mkdir(parents=True)

    assert resolve_dataset(raw, "legacy_model") == ("Task042_legacy_model", "042")
