import logging
logger = logging.getLogger(__name__)
import torch
import pickle
import os
from transformers import AutoTokenizer, AutoModel, QuantoConfig
from voxtell.utils.text_embedding import last_token_pool, wrap_with_instruction

# Container paths (mounted via docker-compose volumes); override via env vars for local runs.
TEXT_MODEL_PATH = os.environ.get("TEXT_MODEL_PATH", "/models/qwen3-4b-embedding")
VOXTELL_DIR     = os.environ.get("VOXTELL_DIR",     "/models/voxtell_v1.1")
CACHE_OUTPUT_PATH = os.path.join(VOXTELL_DIR, "text_embeddings_cache.pkl")

# add any anatomical structures you think you will ever need to segment here
TARGET_ORGANS = [
    # Core abdominal organs
    "spleen", "liver", "left kidney", "right kidney", "pancreas", "gallbladder",
    "duodenum", "stomach", "large intestine", "small intestine", "rectum",
    # Vascular
    "aorta", "pulmonary artery", "superior vena cava", "inferior vena cava",
    "hepatic vessels", "portal vessels",
    # Thoracic
    "lung", "left lung", "right lung", "trachea", "esophagus", "heart",
    # Adrenal / Urinary
    "right adrenal gland", "left adrenal gland", "urinary bladder",
    # Breast
    "left breast", "right breast",
    # Bones / Skeleton
    "bones", "sacrum", "left hipbone", "right hipbone", "mandible", "spinal canal", "spinal cord",
    # Brain / Head & Neck
    "brainstem", "chiasma opticum",
    "left cochlea", "right cochlea",
    "left eye", "right eye",
    "left lacrimal gland", "right lacrimal gland",
    "left submandibular gland", "right submandibular gland",
    "left parotid gland", "right parotid gland",
    "left lens", "right lens",
    "left optic nerve", "right optic nerve",
    "larynx", "lips",
    "superior constrictor muscle", "middle constrictor muscle", "inferior constrictor muscle",
    # Cardiac chambers
    "left ventricle", "right ventricle", "left atrium", "right atrium",
    # Oncology / Lesions
    "tumor", "lesion", "soft tissue lesion", "carcinoma",
    "lung nodule", "lung lesion", "pulmonary nodule", "nodule",
    "liver tumor", "lung tumor", "breast tumor",
    "hepatocellular carcinoma", "pancreas tumor", "esophageal cancer",
    "fdg-avid tumor", "fdg-avid breast cancer",
    "breast cancer", "breast cancer metastasis",
    "metastases", "liver metastasis", "brain metastasis", "lymph node metastasis",
    "lymph nodes", "lymph node", "chest lymph node",
    "MS lesion",
    # Renal / Kidney oncology
    "renal tumor", "kidney tumor", "renal mass", "renal cell carcinoma",
    "kidney lesion", "renal lesion",
    # Hepatic / Liver oncology
    "liver lesion", "hepatic lesion", "hepatic tumor", "focal liver lesion",
    "liver cancer", "liver metastasis",
    # Skin / Other
    "skin", "organ",
]

logger.info("Loading Qwen3-4B directly to GPU to generate permanent cache...")
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

logger.info("Generating mathematical vectors...")
for organ in TARGET_ORGANS:
    logger.info(f" -> Embedding: {organ}")
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

logger.info(f"\nSUCCESS! Cache saved to {CACHE_OUTPUT_PATH}")
logger.info("You can now safely delete the text model from your container's RAM pipeline!")