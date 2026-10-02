from __future__ import annotations

from pathlib import Path

import pytest

from agent_system.toolkit.context import use_context
from agent_system.toolpacks.common import workspace as module
from tests.support.toolkit_fixtures import make_context


@pytest.mark.unit
def test_workspace_helper_maps_logical_and_legacy_paths(tmp_path):
    workspace = tmp_path / "workspace_session"
    workspace.mkdir()

    with use_context(make_context(workspace_dir=workspace)):
        direct = module.base_on_workspace_dir("reports/final.pdf")
        legacy = module.base_on_workspace_dir(
            "old/prefix/workspace_session/masks/spleen.nii.gz"
        )

    assert Path(direct) == workspace / "outputs" / "reports" / "final.pdf"
    assert Path(legacy) == workspace / "data" / "masks" / "spleen.nii.gz"


@pytest.mark.unit
def test_workspace_helper_handles_empty_values(tmp_path):
    with use_context(make_context(workspace_dir=tmp_path)):
        assert module.base_on_workspace_dir(None) is None
        assert module.base_on_workspace_dir("") is None
        assert module.base_on_workspace_dir([]) is None
