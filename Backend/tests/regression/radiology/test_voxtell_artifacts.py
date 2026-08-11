import json
import os
import tempfile
import unittest
import zipfile

from lib.configs.voxtell_artifacts import (  # noqa: E402
    VOXTELL_REQUIRED_FILES,
    ensure_voxtell_artifacts,
    find_incomplete_voxtell_files,
)


class VoxTellArtifactsTest(unittest.TestCase):
    @staticmethod
    def write_complete_snapshot(models_dir: str) -> None:
        for relative_path in VOXTELL_REQUIRED_FILES:
            path = os.path.join(models_dir, *relative_path.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if relative_path.endswith("plans.json"):
                with open(path, "w", encoding="utf-8") as stream:
                    json.dump({"plans": "present"}, stream)
            elif relative_path.endswith("labels.json"):
                with open(path, "w", encoding="utf-8") as stream:
                    json.dump(["liver"], stream)
            elif relative_path.endswith((".pth", ".npz")):
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("tensor", b"present")
            else:
                with open(path, "w", encoding="utf-8") as stream:
                    stream.write("present")

    def test_missing_files_inside_existing_directories_are_detected(self):
        with tempfile.TemporaryDirectory() as models_dir:
            os.makedirs(os.path.join(models_dir, "voxtell_v1.1", "fold_0"))
            missing = find_incomplete_voxtell_files(models_dir)

            self.assertIn("voxtell_v1.1/plans.json", missing)
            self.assertIn("voxtell_v1.1/fold_0/checkpoint_final.pth", missing)
            self.assertIn("voxtell_v1.1/fold_0/INFO.txt", missing)

    def test_legacy_v1_and_npz_embeddings_are_not_required(self):
        self.assertFalse(any(path.startswith("voxtell_v1.0/") for path in VOXTELL_REQUIRED_FILES))
        self.assertFalse(any(path.startswith("embeddings/") for path in VOXTELL_REQUIRED_FILES))

    def test_incomplete_snapshot_is_downloaded_and_verified(self):
        with tempfile.TemporaryDirectory() as models_dir:
            calls = []

            def downloader(**kwargs):
                calls.append(kwargs)
                self.write_complete_snapshot(models_dir)
                return models_dir

            ensure_voxtell_artifacts(models_dir, downloader=downloader, log=lambda _: None)

            self.assertEqual(1, len(calls))
            self.assertEqual([], find_incomplete_voxtell_files(models_dir))

    def test_truncated_checkpoint_is_removed_before_download(self):
        with tempfile.TemporaryDirectory() as models_dir:
            self.write_complete_snapshot(models_dir)
            checkpoint_relative = next(
                path
                for path in VOXTELL_REQUIRED_FILES
                if path.endswith("checkpoint_final.pth")
            )
            checkpoint = os.path.join(
                models_dir,
                *checkpoint_relative.split("/"),
            )
            with open(checkpoint, "rb+") as stream:
                stream.truncate(10)

            invalid_was_removed = []

            def downloader(**_kwargs):
                invalid_was_removed.append(not os.path.exists(checkpoint))
                self.write_complete_snapshot(models_dir)
                return models_dir

            ensure_voxtell_artifacts(models_dir, downloader=downloader, log=lambda _: None)
            self.assertEqual([True], invalid_was_removed)

    def test_complete_snapshot_does_not_download_again(self):
        with tempfile.TemporaryDirectory() as models_dir:
            self.write_complete_snapshot(models_dir)

            def unexpected_download(**_kwargs):
                self.fail("complete VoxTell artifacts should not be downloaded again")

            ensure_voxtell_artifacts(
                models_dir,
                downloader=unexpected_download,
                log=lambda _: None,
            )


if __name__ == "__main__":
    unittest.main()
