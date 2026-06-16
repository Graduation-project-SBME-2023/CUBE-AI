import torch
import pickle
import os
from transformers import AutoTokenizer, AutoModel, QuantoConfig
from voxtell.utils.text_embedding import last_token_pool, wrap_with_instruction

# local paths  todo: make this generic
TEXT_MODEL_PATH = r"D:\GP_2026\Github\agenticsystem\OHIF-Standalone\Backend\models\qwen3-4b-embedding"
VOXTELL_DIR = r"D:\GP_2026\Github\agenticsystem\OHIF-Standalone\Backend\models\voxtell_v1.1"
CACHE_OUTPUT_PATH = os.path.join(VOXTELL_DIR, "text_embeddings_cache.pkl")

# add any anatomical structures you think you will ever need to segment here
TARGET_ORGANS = ["spleen", "liver", "kidney", "aorta", "tumor", "pancreas", "gallbladder"] 

print("Loading Qwen3-4B directly to GPU to generate permanent cache...")
tokenizer = AutoTokenizer.from_pretrained(TEXT_MODEL_PATH, padding_side='left', local_files_only=True)
q_config = QuantoConfig(weights="int4")
text_backbone = AutoModel.from_pretrained(
    TEXT_MODEL_PATH, 
    device_map="cuda", 
    quantization_config=q_config, 
    local_files_only=True
)
text_backbone.eval()

cached_embeddings = {}

print("Generating mathematical vectors...")
for organ in TARGET_ORGANS:
    print(f" -> Embedding: {organ}")
    wrapped_text = wrap_with_instruction([organ])
    text_tokens = tokenizer(wrapped_text, padding=True, truncation=True, max_length=512, return_tensors="pt")
    text_tokens = {k: v.to("cuda") for k, v in text_tokens.items()}
    
    with torch.no_grad():
        text_embed = text_backbone(**text_tokens)
        embeddings = last_token_pool(text_embed.last_hidden_state, text_tokens["attention_mask"])
        
    # save as 1x1x2560 floating tensor on CPU to save memory
    cached_embeddings[organ] = embeddings.float().view(1, 1, -1).to("cpu")

with open(CACHE_OUTPUT_PATH, "wb") as f:
    pickle.dump(cached_embeddings, f)

print(f"\nSUCCESS! Cache saved to {CACHE_OUTPUT_PATH}")
print("You can now safely delete the text model from your container's RAM pipeline!")