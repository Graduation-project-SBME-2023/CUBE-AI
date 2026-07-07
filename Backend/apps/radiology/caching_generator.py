import torch
import pickle
import os
from transformers import AutoTokenizer, AutoModel
from voxtell.utils.text_embedding import last_token_pool, wrap_with_instruction

# Container paths (mounted via docker-compose volumes)
TEXT_MODEL_PATH = os.environ.get("TEXT_MODEL_PATH", "/models/qwen3-4b-embedding")
VOXTELL_DIR     = os.environ.get("VOXTELL_DIR",     "/models/voxtell_v1.1")
CACHE_OUTPUT_PATH = os.path.join(VOXTELL_DIR, "text_embeddings_cache.pkl")

# add any anatomical structures you think you will ever need to segment here
TARGET_ORGANS = []

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
    with open(CACHE_OUTPUT_PATH, "rb") as f:
        cached_embeddings = pickle.load(f)
    print(f"Loaded existing cache with {len(cached_embeddings)} prompts.")
else:
    cached_embeddings = {}
    print("No existing cache found. Starting fresh.")

new_prompts = [p for p in TARGET_ORGANS if p.lower().strip() not in cached_embeddings]
if not new_prompts:
    print("\nAll prompts already cached — nothing to do!")
else:
    print(f"Generating mathematical vectors for {len(new_prompts)} NEW prompt(s)...")
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
        cached_embeddings[key] = embeddings.float().view(1, 1, -1).to("cpu")

    with open(CACHE_OUTPUT_PATH, "wb") as f:
        pickle.dump(cached_embeddings, f)
    print(f"\nSUCCESS! Cache now has {len(cached_embeddings)} prompts → {CACHE_OUTPUT_PATH}")
    print("Restart the monailabel container so the server picks up the new cache.")
print("You can now safely delete the text model from your container's RAM pipeline!")