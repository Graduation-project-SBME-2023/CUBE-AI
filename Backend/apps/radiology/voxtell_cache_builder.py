import torch
import pickle
import os
import tempfile

from contextlib import contextmanager
from transformers import AutoTokenizer, AutoModel
from voxtell.utils.text_embedding import last_token_pool, wrap_with_instruction
from lib.configs.qwen_embedding_model import ensure_qwen_embedding_model
from lib.configs.voxtell_supported_labels import VOXTELL_SUPPORTED_LABELS

# Container paths (mounted via docker-compose volumes)
TEXT_MODEL_PATH = os.environ.get("TEXT_MODEL_PATH", "/models/qwen3-4b-embedding")

# Validate the required tokenizer, index, and weight files. This runs before
# the cache builder loads the model and resumes any interrupted download.
ensure_qwen_embedding_model(TEXT_MODEL_PATH)
VOXTELL_DIR     = os.environ.get("VOXTELL_DIR",     "/models/voxtell_v1.1")
CACHE_OUTPUT_PATH = os.path.join(VOXTELL_DIR, "text_embeddings_cache.pkl")


@contextmanager
def exclusive_cache_process_lock(cache_path: str):
    """Serialize cache updates with the same sidecar used at inference time."""
    lock_path = f"{cache_path}.lock"
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "a+b") as lock_stream:
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


def load_exact_cache(cache_path: str) -> dict:
    if not os.path.exists(cache_path):
        return {}
    with open(cache_path, "rb") as stream:
        raw_cache = pickle.load(stream)
    if not isinstance(raw_cache, dict):
        raise TypeError("VoxTell cache must be a dictionary")
    return {
        str(key).strip().lower(): embedding
        for key, embedding in raw_cache.items()
        if str(key).strip()
    }


def save_cache_atomically(cache_path: str, cache: dict) -> None:
    cache_dir = os.path.dirname(cache_path)
    os.makedirs(cache_dir, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=cache_dir,
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


# Keep the pre-generated cache aligned with the VoxTell model configuration.
TARGET_ORGANS = list(VOXTELL_SUPPORTED_LABELS)

print("Loading Qwen3-4B directly to GPU to generate permanent cache...")
tokenizer = AutoTokenizer.from_pretrained(TEXT_MODEL_PATH, padding_side='left', local_files_only=True)
# Use bfloat16 — QuantoConfig int4 is incompatible with this container's transformers version.
# This is a one-time script; the ~4 GB VRAM peak is acceptable.
text_backbone = AutoModel.from_pretrained(
    TEXT_MODEL_PATH,
    device_map="cuda",
    torch_dtype=torch.bfloat16,
    local_files_only=True,
)
text_backbone.eval()

# ── Incremental mode: load existing cache and skip already-embedded prompts ──
if os.path.exists(CACHE_OUTPUT_PATH):
    cached_embeddings = load_exact_cache(CACHE_OUTPUT_PATH)
    print(f"Loaded existing cache with {len(cached_embeddings)} prompts.")
else:
    cached_embeddings = {}
    print("No existing cache found. Starting fresh.")

new_prompts = [p for p in TARGET_ORGANS if p.lower().strip() not in cached_embeddings]
if not new_prompts:
    print("\nAll prompts already cached — nothing to do!")
else:
    print(f"Generating mathematical vectors for {len(new_prompts)} NEW prompt(s)...")
    generated_embeddings = {}
    for organ in new_prompts:
        key = organ.lower().strip()
        print(f" -> Embedding: {organ}")
        wrapped_text = wrap_with_instruction([organ])
        text_tokens = tokenizer(wrapped_text, padding=True, truncation=True, max_length=512, return_tensors="pt")
        text_tokens = {k: v.to("cuda") for k, v in text_tokens.items()}

        with torch.no_grad():
            text_embed = text_backbone(**text_tokens)
            embeddings = last_token_pool(text_embed.last_hidden_state, text_tokens["attention_mask"])

        # store as 1x1xD float32 tensor on CPU
        generated_embeddings[key] = embeddings.float().view(1, 1, -1).to("cpu")

    # Reload after acquiring the inter-process lock so a concurrent dynamic
    # inference update is not lost. Atomic replacement prevents partial reads.
    with exclusive_cache_process_lock(CACHE_OUTPUT_PATH):
        cached_embeddings = load_exact_cache(CACHE_OUTPUT_PATH)
        cached_embeddings.update(generated_embeddings)
        save_cache_atomically(CACHE_OUTPUT_PATH, cached_embeddings)
    print(f"\nSUCCESS! Cache now has {len(cached_embeddings)} prompts → {CACHE_OUTPUT_PATH}")
    print("Restart the monailabel container so the server picks up the new cache.")
print("You can now safely delete the text model from your container's RAM pipeline!")
