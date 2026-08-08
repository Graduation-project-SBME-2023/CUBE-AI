"""Download and validate the local Qwen3 embedding model snapshot."""

import json
import os
import struct
from typing import Callable, List, Optional


QWEN_EMBEDDING_REPO = "Qwen/Qwen3-Embedding-4B"
QWEN_REQUIRED_FILES = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "model.safetensors.index.json",
    "model-00001-of-00002.safetensors",
    "model-00002-of-00002.safetensors",
)


def _is_complete_safetensors(path: str) -> bool:
    """Check that a safetensors file contains all bytes declared by its header."""
    try:
        file_size = os.path.getsize(path)
        with open(path, "rb") as stream:
            header_size_bytes = stream.read(8)
            if len(header_size_bytes) != 8:
                return False
            header_size = struct.unpack("<Q", header_size_bytes)[0]
            if header_size == 0 or header_size > file_size - 8:
                return False
            header = json.loads(stream.read(header_size).decode("utf-8"))

        data_end = 0
        tensor_count = 0
        for name, metadata in header.items():
            if name == "__metadata__":
                continue
            offsets = metadata["data_offsets"]
            if len(offsets) != 2 or offsets[0] < 0 or offsets[1] < offsets[0]:
                return False
            data_end = max(data_end, offsets[1])
            tensor_count += 1
        return tensor_count > 0 and file_size >= 8 + header_size + data_end
    except (OSError, KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def find_incomplete_model_files(model_dir: str) -> List[str]:
    """Return required files that are absent, empty, or invalid."""
    incomplete = []
    for relative_path in QWEN_REQUIRED_FILES:
        path = os.path.join(model_dir, relative_path)
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            incomplete.append(relative_path)
        elif relative_path.endswith(".safetensors") and not _is_complete_safetensors(path):
            incomplete.append(relative_path)

    index_name = "model.safetensors.index.json"
    index_path = os.path.join(model_dir, index_name)
    if index_name not in incomplete:
        try:
            with open(index_path, "r", encoding="utf-8") as stream:
                weight_map = json.load(stream)["weight_map"]
            if not isinstance(weight_map, dict):
                raise TypeError("weight_map must be an object")
            declared_shards = set(weight_map.values())
            expected_shards = {
                "model-00001-of-00002.safetensors",
                "model-00002-of-00002.safetensors",
            }
            if not expected_shards.issubset(declared_shards):
                incomplete.append(index_name)
            for shard in declared_shards:
                shard_path = os.path.join(model_dir, shard)
                if shard not in incomplete and (
                    not os.path.isfile(shard_path) or not _is_complete_safetensors(shard_path)
                ):
                    incomplete.append(shard)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            incomplete.append(index_name)

    return incomplete


def ensure_qwen_embedding_model(
    model_dir: str,
    *,
    downloader: Optional[Callable[..., str]] = None,
    log: Callable[[str], None] = print,
) -> str:
    """Ensure the complete Qwen snapshot exists, resuming it when necessary."""
    incomplete = find_incomplete_model_files(model_dir)
    if not incomplete:
        log(f"Qwen embedding model is complete at {model_dir}")
        return model_dir

    log(
        "Qwen embedding model is incomplete; downloading/resuming missing files: "
        + ", ".join(incomplete)
    )
    os.makedirs(model_dir, exist_ok=True)

    # Remove only files proven invalid. Hugging Face can otherwise trust stale
    # local metadata and skip a truncated destination that still exists.
    for relative_path in incomplete:
        invalid_path = os.path.join(model_dir, relative_path)
        if os.path.isfile(invalid_path):
            log(f"Removing incomplete model file before retry: {invalid_path}")
            os.remove(invalid_path)

    if downloader is None:
        from huggingface_hub import snapshot_download

        downloader = snapshot_download

    downloader(
        repo_id=QWEN_EMBEDDING_REPO,
        ignore_patterns=["*.gguf"],
        local_dir=model_dir,
    )

    incomplete = find_incomplete_model_files(model_dir)
    if incomplete:
        raise RuntimeError(
            f"Qwen embedding model download is incomplete at {model_dir}. "
            "Missing or invalid files: " + ", ".join(incomplete)
        )

    log(f"Qwen embedding model download verified at {model_dir}")
    return model_dir
