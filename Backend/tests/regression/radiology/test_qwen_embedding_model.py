import json
import os
import struct
import tempfile
import unittest

from lib.configs.qwen_embedding_model import (  # noqa: E402
    QWEN_REQUIRED_FILES,
    ensure_qwen_embedding_model,
    find_incomplete_model_files,
)


class QwenEmbeddingModelTest(unittest.TestCase):
    @staticmethod
    def write_complete_snapshot(model_dir: str) -> None:
        for relative_path in QWEN_REQUIRED_FILES:
            path = os.path.join(model_dir, relative_path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as stream:
                if relative_path.endswith(".safetensors"):
                    header = json.dumps(
                        {
                            "tensor": {
                                "dtype": "F32",
                                "shape": [1],
                                "data_offsets": [0, 4],
                            }
                        }
                    ).encode("utf-8")
                    stream.write(struct.pack("<Q", len(header)))
                    stream.write(header)
                    stream.write(b"data")
                else:
                    stream.write(b"present")

        index_path = os.path.join(model_dir, "model.safetensors.index.json")
        with open(index_path, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "weight_map": {
                        "layer.0": "model-00001-of-00002.safetensors",
                        "layer.1": "model-00002-of-00002.safetensors",
                    }
                },
                stream,
            )

    def test_existing_directory_with_missing_weight_is_incomplete(self):
        with tempfile.TemporaryDirectory() as model_dir:
            self.write_complete_snapshot(model_dir)
            os.unlink(os.path.join(model_dir, "model-00002-of-00002.safetensors"))

            self.assertIn(
                "model-00002-of-00002.safetensors",
                find_incomplete_model_files(model_dir),
            )

    def test_truncated_weight_is_incomplete(self):
        with tempfile.TemporaryDirectory() as model_dir:
            self.write_complete_snapshot(model_dir)
            weight_path = os.path.join(model_dir, "model-00001-of-00002.safetensors")
            with open(weight_path, "rb+") as stream:
                stream.truncate(os.path.getsize(weight_path) - 2)

            self.assertIn(
                "model-00001-of-00002.safetensors",
                find_incomplete_model_files(model_dir),
            )

    def test_incomplete_snapshot_is_downloaded_and_verified(self):
        with tempfile.TemporaryDirectory() as model_dir:
            calls = []

            def downloader(**kwargs):
                calls.append(kwargs)
                self.write_complete_snapshot(model_dir)
                return model_dir

            ensure_qwen_embedding_model(model_dir, downloader=downloader, log=lambda _: None)

            self.assertEqual(1, len(calls))
            self.assertEqual([], find_incomplete_model_files(model_dir))

    def test_complete_snapshot_does_not_download_again(self):
        with tempfile.TemporaryDirectory() as model_dir:
            self.write_complete_snapshot(model_dir)

            def unexpected_download(**_kwargs):
                self.fail("complete model should not be downloaded again")

            ensure_qwen_embedding_model(
                model_dir,
                downloader=unexpected_download,
                log=lambda _: None,
            )


if __name__ == "__main__":
    unittest.main()
