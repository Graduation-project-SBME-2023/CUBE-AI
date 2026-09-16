"""The runtime half of the write cutover."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import storage_write_guard
from storage_paths import HYPOTHESES_DIR, ws_sub
from storage_write_guard import RetiredWorkspaceWrite, check_write, retired_entry

pytestmark = pytest.mark.unit


def _path(logical: str) -> Path:
    return ws_sub("guard-session", logical)


def test_a_retired_path_is_recognised_with_the_table_that_replaced_it():
    entry = retired_entry(_path("config.json"))
    assert entry is not None
    pattern, replacement, step = entry
    assert pattern == "state/config.json"
    assert "cohorts" in replacement and step == "H"


@pytest.mark.parametrize(
    "logical",
    [
        "config.json",
        "populations/cohort_labels.csv",
        "populations/targets/targets.csv",
        "segmentation_manifest.json",
        "review_draft.json",
        "review_result.json",
        "scratchpad.json",
        "state.json",
        "outputs/segmentation_agent_output.json",
        "temp_segmentations/segmentation_manifest.json.tmp",
    ],
)
def test_writing_a_retired_workspace_file_raises(logical):
    with pytest.raises(RetiredWorkspaceWrite) as excinfo:
        check_write(_path(logical))
    assert "no file fallback" in str(excinfo.value)


@pytest.mark.parametrize(
    "logical",
    [
        "volumes/volume-1.nii.gz",
        "masks/key.nii.gz",
        "overlays/key.gif",
        # NOTE: "reports/report.tex" was here before step K was implemented.
        # It is now retired by K (outputs/reports/*.tex → reports.source_text).
        # The K-specific test_write_guard_k.py verifies it raises correctly.
        "temp_segmentations/seg_key_modified.nii.gz",
    ],
)
def test_disposable_and_not_yet_retired_paths_are_untouched(logical):
    """By path and purpose, never by extension: a `.json` or `.csv` elsewhere
    in the workspace is not affected."""
    check_write(_path(logical))


def test_a_path_outside_a_session_workspace_is_not_the_guards_business(tmp_path):
    check_write(tmp_path / "config.json")
    check_write(HYPOTHESES_DIR / "config.json")


def test_the_audit_hook_only_fires_on_writes_renames_and_copies():
    retired = str(_path("config.json"))
    # Reads stay legal.
    storage_write_guard._hook("open", (retired, "r", os.O_RDONLY))
    storage_write_guard._hook("open", (retired, "rb", os.O_RDONLY))

    for mode in ("w", "a", "x", "r+"):
        with pytest.raises(RetiredWorkspaceWrite):
            storage_write_guard._hook("open", (retired, mode, os.O_WRONLY))
    # os.open passes no mode string, only flags.
    with pytest.raises(RetiredWorkspaceWrite):
        storage_write_guard._hook("open", (retired, None, os.O_WRONLY | os.O_CREAT))
    # An atomic write renames a temp file onto the retired path.
    with pytest.raises(RetiredWorkspaceWrite):
        storage_write_guard._hook("os.replace", ("/tmp/x.tmp", retired))
    with pytest.raises(RetiredWorkspaceWrite):
        storage_write_guard._hook("shutil.copyfile", ("/tmp/x", retired))
    # An unrelated event is ignored.
    storage_write_guard._hook("os.listdir", (retired,))


def test_the_guard_catches_a_real_write_through_any_library(tmp_path, monkeypatch):
    """Installed, it sees json.dump, Path.write_text and csv alike — that is
    what makes it a guard rather than a list of call sites."""
    storage_write_guard.install_write_guard()
    assert storage_write_guard.is_installed()

    target = _path("config.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    with pytest.raises(RetiredWorkspaceWrite):
        with open(target, "w", encoding="utf-8") as stream:
            json.dump({"datasetlist": []}, stream)
    with pytest.raises(RetiredWorkspaceWrite):
        target.write_text("{}", encoding="utf-8")
    assert not target.exists()

    # A disposable path in the same workspace still writes normally.
    volume = _path("volumes/v.nii.gz")
    volume.parent.mkdir(parents=True, exist_ok=True)
    volume.write_bytes(b"data")
    assert volume.read_bytes() == b"data"
