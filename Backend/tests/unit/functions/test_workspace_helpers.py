from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.mcp_loader import load_mcp_tool


module = load_mcp_tool("helpers.workspace")


@pytest.mark.unit
def test_workspace_helper_maps_logical_and_legacy_paths(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace_session"
    workspace.mkdir()
    monkeypatch.setattr(module, "WORKSPACE_DIR", str(workspace))

    direct = module.base_on_workspace_dir("reports/final.pdf")
    legacy = module.base_on_workspace_dir(
        "old/prefix/workspace_session/masks/spleen.nii.gz"
    )

    assert Path(direct) == workspace / "outputs" / "reports" / "final.pdf"
    assert Path(legacy) == workspace / "data" / "masks" / "spleen.nii.gz"


@pytest.mark.unit
def test_workspace_helper_handles_empty_values(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "WORKSPACE_DIR", str(tmp_path))
    assert module.base_on_workspace_dir(None) is None
    assert module.base_on_workspace_dir("") is None
    assert module.base_on_workspace_dir([]) is None

