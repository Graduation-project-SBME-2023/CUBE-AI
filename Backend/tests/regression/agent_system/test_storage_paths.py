import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[3]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from storage_paths import workspace_subpath


class WorkspacePathMappingTests(unittest.TestCase):
    def test_logical_categories_map_to_target_tree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace_session"
            expected = {
                "state.json": workspace / "state" / "state.json",
                "clinical_data.csv": workspace / "data" / "populations" / "clinical_data.csv",
                "populations/targets/targets.csv": workspace / "data" / "populations" / "targets" / "targets.csv",
                "volumes/series.nii.gz": workspace / "data" / "volumes" / "series.nii.gz",
                "masks/mask.nii.gz": workspace / "data" / "masks" / "mask.nii.gz",
                "outputs/agent_output.json": workspace / "outputs" / "agents" / "agent_output.json",
                "overlays/mask.gif": workspace / "outputs" / "overlays" / "mask.gif",
                "features/features.csv": workspace / "outputs" / "features" / "features.csv",
                "modeling/model.pkl": workspace / "outputs" / "modeling" / "model.pkl",
                "reports/report.pdf": workspace / "outputs" / "reports" / "report.pdf",
                "temp_segmentations/edit.nii.gz": workspace / "temp" / "segmentations" / "edit.nii.gz",
            }
            for logical, physical in expected.items():
                with self.subTest(logical=logical):
                    self.assertEqual(
                        workspace_subpath(workspace, logical),
                        physical.resolve(),
                    )

    def test_rejects_retired_workspace_cache_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace_session"
            retired_paths = [
                ".knowledge_cache/query.json",
                "cache/literature/query.json",
            ]
            for logical in retired_paths:
                with self.subTest(logical=logical):
                    with self.assertRaisesRegex(ValueError, "Workspace cache paths are retired"):
                        workspace_subpath(workspace, logical)

    def test_rejects_retired_workspace_literature_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace_session"
            retired_paths = [
                "literature_context.json",
                "literature_review_output/context.json",
                "temp/literature/context.json",
                "outputs/literature/report.md",
            ]
            for logical in retired_paths:
                with self.subTest(logical=logical):
                    with self.assertRaisesRegex(ValueError, "Workspace literature paths are retired"):
                        workspace_subpath(workspace, logical)

    def test_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                workspace_subpath(Path(temp_dir) / "workspace_session", "reports/../state.json")


if __name__ == "__main__":
    unittest.main()
