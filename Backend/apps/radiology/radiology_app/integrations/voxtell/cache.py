from __future__ import annotations

import logging
import os
import pickle
import tempfile
from contextlib import contextmanager
from pathlib import Path

from storage_paths import MODELS_DIR, TEMP_DIR

logger = logging.getLogger(__name__)


@contextmanager
def exclusive_cache_process_lock(cache_path: Path):
    """Serialize cache updates with the sidecar used at inference time."""
    lock_path = TEMP_DIR / "radiology" / "voxtell-cache.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_stream:
        if os.name == "nt":
            import msvcrt

            lock_stream.seek(0, os.SEEK_END)
            if lock_stream.tell() == 0:
                lock_stream.write(b"\0")
                lock_stream.flush()
            lock_stream.seek(0)
            msvcrt.locking(lock_stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock_stream.seek(0)
                msvcrt.locking(lock_stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)


def load_exact_cache(cache_path: Path) -> dict:
    if not cache_path.is_file():
        return {}
    with cache_path.open("rb") as stream:
        raw_cache = pickle.load(stream)
    if not isinstance(raw_cache, dict):
        raise TypeError("VoxTell cache must be a dictionary")
    return {
        str(key).strip().lower(): embedding
        for key, embedding in raw_cache.items()
        if str(key).strip()
    }


def save_cache_atomically(cache_path: Path, cache: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            # os.replace is atomic only when source and destination are on
            # the same filesystem. Model and temp storage may be separate
            # container volumes, so stage beside the destination.
            dir=cache_path.parent,
            prefix=".voxtell-cache-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temp_path = stream.name
            pickle.dump(cache, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, cache_path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def build_voxtell_cache(
    *,
    text_model_path: str | os.PathLike[str] | None = None,
    voxtell_dir: str | os.PathLike[str] | None = None,
) -> int:
    """Build only missing VoxTell embeddings and return the final cache size.

    Heavy ML dependencies are deliberately imported only after the inexpensive
    completeness check, so normal container restarts do not load Torch,
    Transformers, or the 4B text model.
    """
    from lib.configs.voxtell_supported_labels import VOXTELL_SUPPORTED_LABELS

    model_path = Path(
        text_model_path
        or os.environ.get("TEXT_MODEL_PATH", MODELS_DIR / "qwen3-4b-embedding")
    )
    output_dir = Path(
        voxtell_dir
        or os.environ.get("VOXTELL_DIR", MODELS_DIR / "voxtell_v1.1")
    )
    cache_path = output_dir / "text_embeddings_cache.pkl"
    target_prompts = list(VOXTELL_SUPPORTED_LABELS)

    cached_embeddings = load_exact_cache(cache_path)
    missing_prompts = [
        prompt
        for prompt in target_prompts
        if prompt.lower().strip() not in cached_embeddings
    ]
    if not missing_prompts:
        logger.info("VoxTell cache is complete at %s; skipping model loading", cache_path)
        return len(cached_embeddings)

    # These imports account for most startup time and memory.  Keep them below
    # the cache fast path.
    import torch
    from transformers import AutoModel, AutoTokenizer
    from voxtell.utils.text_embedding import last_token_pool, wrap_with_instruction

    from lib.configs.qwen_embedding_model import ensure_qwen_embedding_model

    ensure_qwen_embedding_model(str(model_path))
    device = os.environ.get(
        "TEXT_EMBEDDING_DEVICE",
        "cuda" if torch.cuda.is_available() else "cpu",
    )
    dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
    logger.info("Loading Qwen embedding model on %s", device)
    tokenizer = AutoTokenizer.from_pretrained(
        model_path,
        padding_side="left",
        local_files_only=True,
    )
    text_backbone = AutoModel.from_pretrained(
        model_path,
        device_map=device,
        dtype=dtype,
        local_files_only=True,
    )
    text_backbone.eval()

    generated_embeddings = {}
    for organ in missing_prompts:
        key = organ.lower().strip()
        logger.info("Embedding VoxTell prompt: %s", organ)
        wrapped_text = wrap_with_instruction([organ])
        text_tokens = tokenizer(
            wrapped_text,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        text_tokens = {name: value.to(device) for name, value in text_tokens.items()}
        with torch.no_grad():
            text_output = text_backbone(**text_tokens)
            embeddings = last_token_pool(
                text_output.last_hidden_state,
                text_tokens["attention_mask"],
            )
        generated_embeddings[key] = embeddings.float().view(1, 1, -1).cpu()

    # Reload while holding the process lock so concurrent inference updates are
    # retained. Atomic replacement prevents readers from seeing partial data.
    with exclusive_cache_process_lock(cache_path):
        cached_embeddings = load_exact_cache(cache_path)
        cached_embeddings.update(generated_embeddings)
        save_cache_atomically(cache_path, cached_embeddings)

    logger.info("VoxTell cache now contains %s prompts", len(cached_embeddings))
    return len(cached_embeddings)
