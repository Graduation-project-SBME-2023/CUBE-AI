import json
import os
import tempfile
import unittest
import zipfile

from lib.configs.model_artifacts import (  # noqa: E402
    NNINTERACTIVE_REQUIRED_FILES,
    ensure_model_archive,
    ensure_nninteractive_artifacts,
    find_incomplete_nninteractive_files,
    is_complete_model_archive,
)


def write_archive(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("checkpoint/data.pkl", b"present")


class ModelArchiveTest(unittest.TestCase):
    def test_truncated_existing_checkpoint_is_redownloaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "model.pt")
            write_archive(path)
            with open(path, "rb+") as stream:
                stream.truncate(10)

            removed_before_download = []

            def download():
                removed_before_download.append(not os.path.exists(path))
                write_archive(path)

            ensure_model_archive(
                path,
                download,
                description="test checkpoint",
                log=lambda _: None,
            )
            self.assertEqual([True], removed_before_download)
            self.assertTrue(is_complete_model_archive(path))

    def test_failed_download_does_not_pass_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "model.pt")

            def incomplete_download():
                with open(path, "wb") as stream:
                    stream.write(b"partial")

            with self.assertRaises(RuntimeError):
                ensure_model_archive(
                    path,
                    incomplete_download,
                    description="test checkpoint",
                    log=lambda _: None,
                )


class NNInteractiveArtifactsTest(unittest.TestCase):
    @staticmethod
    def write_complete_snapshot(models_dir: str) -> None:
        for relative_path in NNINTERACTIVE_REQUIRED_FILES:
            path = os.path.join(models_dir, *relative_path.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if relative_path.endswith(".json"):
                with open(path, "w", encoding="utf-8") as stream:
                    json.dump({"present": True}, stream)
            elif relative_path.endswith(".pth"):
                write_archive(path)
            else:
                with open(path, "w", encoding="utf-8") as stream:
                    stream.write("present")

    def test_complete_snapshot_does_not_download(self):
        with tempfile.TemporaryDirectory() as models_dir:
            self.write_complete_snapshot(models_dir)

            def unexpected_download(**_kwargs):
                self.fail("complete nnInteractive model should not download")

            ensure_nninteractive_artifacts(
                models_dir,
                downloader=unexpected_download,
                log=lambda _: None,
            )

    def test_missing_snapshot_file_is_downloaded(self):
        with tempfile.TemporaryDirectory() as models_dir:
            calls = []

            def downloader(**kwargs):
                calls.append(kwargs)
                self.write_complete_snapshot(models_dir)
                return models_dir

            ensure_nninteractive_artifacts(
                models_dir,
                downloader=downloader,
                log=lambda _: None,
            )
            self.assertEqual(1, len(calls))
            self.assertEqual([], find_incomplete_nninteractive_files(models_dir))


if __name__ == "__main__":
    unittest.main()
