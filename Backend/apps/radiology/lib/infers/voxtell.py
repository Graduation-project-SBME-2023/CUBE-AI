# # # Copyright (c) MONAI Consortium
# # # Licensed under the Apache License, Version 2.0 (the "License");
# # # you may not use this file except in compliance with the License.
# # # You may obtain a copy of the License at
# # #     http://www.apache.org/licenses/LICENSE-2.0
# # # Unless required by applicable law or agreed to in writing, software
# # # distributed under the License is distributed on an "AS IS" BASIS,
# # # WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# # # See the License for the specific language governing permissions and
# # # limitations under the License.
import os
import sys
import gc
import time
import pickle
import ctypes
import threading
import platform
import logging

os.environ["TORCH_CUDA_ARCH_LIST"] = "8.0;8.6;8.9;9.0"
os.environ["QUANTO_DISABLE_CUDA"] = "1"
os.environ["CUDA_HOME"] = "/usr/local/cuda"

import torch
import numpy as np
import SimpleITK as sitk
import psutil

from enum import Enum
from typing import Callable, Sequence, List, Union
from unittest.mock import patch, MagicMock

from transformers import AutoModel, AutoTokenizer

from voxtell.inference.predictor import VoxTellPredictor
from voxtell.utils.text_embedding import last_token_pool, wrap_with_instruction
from acvl_utils.cropping_and_padding.bounding_boxes import insert_crop_into_image

from monai.inferers import SlidingWindowInferer
from monailabel.interfaces.tasks.infer_v2 import InferType
from monailabel.tasks.infer.basic_infer import BasicInferTask

# Medical vocabulary is maintained centrally — do not duplicate synonyms here.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../agent_system/agents/execution_agents/segmentation")))
try:
    from medical_vocabulary import VOXTELL_PROMPT_ALIASES, FLAT_MEDICAL_SYNONYMS
except ImportError:
    # Fallback if run outside the agent_system context (e.g. standalone MONAI)
    VOXTELL_PROMPT_ALIASES = {}
    FLAT_MEDICAL_SYNONYMS = {}


logger = logging.getLogger(__name__)

# ── Module-level predictor singleton ──────────────────────────────────────────
# MONAI Label creates a NEW instance of the `voxtell` class on every HTTP
# request, which means instance variables (self.xxx) are reset to None each
# time. Storing the predictor here at module level ensures it is loaded ONCE
# and reused across all subsequent requests, preventing CUDA OOM errors.
_PREDICTOR_SINGLETON: "MyVoxTellPredictor | None" = None
_PREDICTOR_LOCK = threading.Lock()
# ──────────────────────────────────────────────────────────────────────────────


def check_memory(step_name: str, device: torch.device):
    """Helper function to print current System RAM and GPU VRAM usage."""
    ram = psutil.virtual_memory()
    ram_used_gb = ram.used / (1024 ** 3)
    ram_total_gb = ram.total / (1024 ** 3)
    log_message = f"[{step_name}] RAM Used: {ram_used_gb:.2f} GB / {ram_total_gb:.2f} GB ({ram.percent}%)"

    if device.type == 'cuda':
        vram_allocated = torch.cuda.memory_allocated(device) / (1024 ** 3)
        vram_reserved = torch.cuda.memory_reserved(device) / (1024 ** 3)
        log_message += f" || VRAM Allocated: {vram_allocated:.2f} GB (Reserved: {vram_reserved:.2f} GB)"

    logger.info(log_message)

class CallBackTypes(str, Enum):
    PRE_TRANSFORMS = "PRE_TRANSFORMS"
    INFERER = "INFERER"
    INVERT_TRANSFORMS = "INVERT_TRANSFORMS"
    POST_TRANSFORMS = "POST_TRANSFORMS"
    WRITER = "WRITER"


class ImageProcessor:
    """Handles image loading, conversion, and processing."""

    @staticmethod
    def load_image_in_ras(image_path):
        img = sitk.ReadImage(image_path)
        orig_orient = sitk.DICOMOrientImageFilter_GetOrientationFromDirectionCosines(
            img.GetDirection()
        )
        img_ras = sitk.DICOMOrient(img, "RAS")
        img_np = sitk.GetArrayFromImage(img_ras)[None]
        return img_np, img_ras, orig_orient

    @staticmethod
    def convert_seg_to_orginal_orientation(segmentation, original_img_in_ras, orig_orient):
        seg_sitk = sitk.GetImageFromArray(segmentation[0])
        seg_sitk.CopyInformation(original_img_in_ras)
        seg_final = sitk.DICOMOrient(seg_sitk, orig_orient)
        return seg_final


class MyVoxTellPredictor(VoxTellPredictor):

    def __init__(
            self,
            model_dir: str,
            device: torch.device = torch.device("cuda"),
            text_encoding_model: str = "Qwen/Qwen3-Embedding-0.6B"
        ):
        # Save these for the lazy-loader later
        self.text_encoding_model_path = text_encoding_model
        self.cache_output_path = os.path.join(model_dir, "text_embeddings_cache.pkl")
        self.tokenizer = None
        self.text_backbone = None

        # Bypass parent's HuggingFace loading to keep VRAM clean on startup
        with patch('transformers.AutoModel.from_pretrained') as mock_automodel, \
             patch('transformers.AutoTokenizer.from_pretrained') as mock_tokenizer:
            mock_automodel.return_value = MagicMock()
            mock_tokenizer.return_value = MagicMock()
            super().__init__(
                model_dir=model_dir,
                device=device,
                text_encoding_model=self.text_encoding_model_path  # Ignored due to mock
            )

        # The parent class assigned MagicMock() to these.
        # We must set them to None so the lazy-loader triggers when needed.
        self.tokenizer = None
        self.text_backbone = None

        self.perform_everything_on_device = False

        # 1. LOAD THE CACHED VECTORS
        if os.path.exists(self.cache_output_path):
            with open(self.cache_output_path, "rb") as f:
                self.embeddings_cache = pickle.load(f)
            logger.info(f"Loaded cached embeddings for: {list(self.embeddings_cache.keys())}")
        else:
            logger.warning(f"Cache file NOT FOUND at {self.cache_output_path}! Will generate dynamically.")
            self.embeddings_cache = {}

        # 2. MOVE VOXTELL DIRECTLY TO GPU
        self.network = self.network.to(self.device)
        torch.cuda.empty_cache()
        check_memory("After INIT (VoxTell loaded, NO text model in RAM)", torch.device("cuda"))

    # Prompt alias map — imported from medical_vocabulary.py (single source of truth).
    # Every value is a label that actually exists in VOXTELL_SUPPORTED_LABELS.
    # Edit medical_vocabulary.VOXTELL_PROMPT_ALIASES to add new remappings.
    FALLBACK_PROMPT_MAP = VOXTELL_PROMPT_ALIASES

    def generate_text_embedding(self, prompt: str) -> torch.Tensor:
        """Lazily loads the text model onto the CPU to generate missing embeddings."""
        if self.tokenizer is None or self.text_backbone is None:
            logger.info(f"Lazy-loading HuggingFace text model '{self.text_encoding_model_path}' to CPU...")
            self.tokenizer = AutoTokenizer.from_pretrained(self.text_encoding_model_path)
            self.text_backbone = AutoModel.from_pretrained(
                self.text_encoding_model_path,
                torch_dtype=torch.float16,
                low_cpu_mem_usage=True
            ).to("cpu")
            self.text_backbone.eval()

        logger.info(f"Generating embedding for new prompt: '{prompt}'")
        wrapped_text = wrap_with_instruction([prompt])

        text_tokens = self.tokenizer(
            wrapped_text,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )

        # Explicitly keep everything on CPU to protect VRAM
        with torch.no_grad():
            text_output = self.text_backbone(**text_tokens)
            embedding = last_token_pool(
                text_output.last_hidden_state,
                text_tokens["attention_mask"],
            )

        return embedding.float().view(1, 1, -1)

    def embed_text_prompts(
        self,
        text_prompts: Union[List[str], str]
    ) -> torch.Tensor:
        """Retrieves cached vectors or dynamically generates and saves them."""
        if isinstance(text_prompts, str):
            text_prompts = [text_prompts]

        embeddings_list = []
        cache_updated = False

        for prompt in text_prompts:
            key = prompt.lower().strip()

            # Helper to get semantic concepts for matching
            def get_semantic_concepts(l: str) -> set:
                import re
                norm = re.sub(r"\s+", " ", str(l or "").replace("_", " ")).lower().strip()
                return set(FLAT_MEDICAL_SYNONYMS.get(w, w) for w in norm.split())

            target_concepts = get_semantic_concepts(key)
            match_found = False

            # 1. Check if semantically equivalent prompt already exists in cache
            for cached_key in self.embeddings_cache:
                if get_semantic_concepts(cached_key) == target_concepts:
                    logger.info(f"Using semantically equivalent cached key '{cached_key}' for prompt '{key}'")
                    embeddings_list.append(self.embeddings_cache[cached_key])
                    match_found = True
                    break

            if not match_found:
                if key in self.FALLBACK_PROMPT_MAP:
                    fallback_key = self.FALLBACK_PROMPT_MAP[key]
                    logger.info(f"Using fallback '{fallback_key}' for prompt '{key}'")
                    embeddings_list.append(self.embeddings_cache[fallback_key])
                else:
                    logger.warning(f"Prompt '{key}' not found. Generating dynamically...")
                    # Generate on CPU
                    new_embedding = self.generate_text_embedding(key)
                    # Save to cache dictionary
                    self.embeddings_cache[key] = new_embedding.cpu()
                    embeddings_list.append(self.embeddings_cache[key])
                    cache_updated = True

        # Save cache back to disk if new items were added
        if cache_updated:
            try:
                with open(self.cache_output_path, "wb") as f:
                    pickle.dump(self.embeddings_cache, f)
                logger.info(f"Updated text embedding cache saved to {self.cache_output_path}")
            except Exception as e:
                logger.error(f"Failed to save updated cache: {e}")

        # Combine into a single tensor and push to GPU for inference
        embeddings = torch.cat(embeddings_list, dim=1)
        return embeddings.to(self.device)

    def predict_single_image(
        self,
        data: np.ndarray,
        text_prompts: Union[str, List[str]]
    ) -> np.ndarray:
        logger.info(f"Phase 1: Retrieving text embeddings for {text_prompts}...")
        embeddings = self.embed_text_prompts(text_prompts)
        check_memory("AFTER extracting the embedding", torch.device("cuda"))

        logger.info("Phase 2: Preprocessing 3D Medical Image...")
        data, bbox, orig_shape = self.preprocess(data)
        logger.info(f"Preprocessed shape: {data.shape}")
        check_memory("AFTER preprocessing the data", torch.device("cuda"))

        logger.info("Phase 3: Running sliding window inference...")
        prediction = self.predict_sliding_window_return_logits(
            data, embeddings
        ).to("cpu")
        check_memory("AFTER SLIDING WINDOW INFERENCE", torch.device("cuda"))

        with torch.no_grad():
            prediction = torch.sigmoid(prediction.float()) > 0.5

        segmentation_reverted_cropping = np.zeros(
            [prediction.shape[0], *orig_shape],
            dtype=np.uint8
        )
        segmentation_reverted_cropping = insert_crop_into_image(
            segmentation_reverted_cropping, prediction, bbox
        )
        check_memory("AFTER POSTPROCESSING", torch.device("cuda"))
        return segmentation_reverted_cropping


class voxtell(BasicInferTask):
    """Implements Voxtell-based segmentation inside MONAILabel server."""

    def __init__(
            self,
            path,
            text_encoding_model_path=None,
            network=None,
            type=InferType.SEGMENTATION,
            labels=None,
            dimension=3,
            description="Pretrained Voxtell Model for medical image segmentation",
            preload=False,
            **kwargs,
        ):
        super().__init__(
            path=path,
            network=network,
            type=type,
            labels=labels,
            dimension=dimension,
            description=description,
            load_strict=True,
            **kwargs,
        )
        self.annotated_image = None
        self.image_path = None
        self.model_path = path
        self.text_encoding_model_path = text_encoding_model_path

    def initialize_predictor(self, device=torch.device("cuda")):
        """Initializes the predictor strictly ONCE (module-level singleton) to keep
        VRAM consumption stable across requests.

        MONAI Label re-instantiates this class on every HTTP request, so
        instance-level caching (self.voxtell_predictor) was being reset to None
        on every call, causing a full model reload and CUDA OOM. The module-level
        singleton (_PREDICTOR_SINGLETON) survives across all requests.
        """
        global _PREDICTOR_SINGLETON
        if _PREDICTOR_SINGLETON is None:
            with _PREDICTOR_LOCK:
                # Double-checked locking — re-check after acquiring the lock
                if _PREDICTOR_SINGLETON is None:
                    logger.info("Initializing MyVoxTellPredictor for the first time (module singleton)...")
                    _PREDICTOR_SINGLETON = MyVoxTellPredictor(
                        model_dir=self.model_path,
                        device=device,
                        text_encoding_model=self.text_encoding_model_path
                    )
        else:
            logger.info("Predictor already loaded in module singleton. Reusing cached model.")

    def run_inferer(self, data, device="cuda"):
        """
        Run Inferer over pre-processed Data.
        """
        start_time = time.time()

        # 1. Load the 3D image
        image_array, image_ras, orig_orient = ImageProcessor.load_image_in_ras(data["image"])

        # 2. Initialize the predictor (only loads from disk on the very first API request)
        self.initialize_predictor(device=torch.device(device))

        # 3. Retrieve target prompt from inference parameters.
        #    Priority: params["text_prompts"] > params["label"] > data["text_prompts"] > "spleen"
        params = data.get("params", {})
        text_prompts = (
            params.get("text_prompts")       # sent by interactive pipeline as list[str]
            or params.get("label")           # sent by MONAILabel UI as a bare string
            or data.get("text_prompts")      # injected directly into the data dict
            or "spleen"                      # last-resort fallback
        )
        # Normalise: always work with a list internally
        if isinstance(text_prompts, str):
            text_prompts = [text_prompts]
        logger.info(f"Resolved text_prompts for inference: {text_prompts}")

        # 4. Run inference using the shared module-level singleton
        voxtell_seg_np_ras = _PREDICTOR_SINGLETON.predict_single_image(image_array, text_prompts)

        # 5. Convert orientation back to original DICOM space
        seg_final = ImageProcessor.convert_seg_to_orginal_orientation(voxtell_seg_np_ras, image_ras, orig_orient)
        seg_np = sitk.GetArrayFromImage(seg_final)
        seg_np = seg_np.transpose(2, 1, 0)
        seg_np = seg_np[None].astype(np.uint8)
        data["pred"] = torch.from_numpy(seg_np)

        # =====================================================================
        # POST-REQUEST CLEANUP (cleans activations, keeps the model loaded)
        # The singleton stays warm in GPU memory for the next request.
        # =====================================================================
        logger.info("Performing post-inference memory optimization...")
        gc.collect()
        torch.cuda.empty_cache()

        if platform.system() == "Linux":
            try:
                ctypes.cdll.LoadLibrary("libc.so.6").malloc_trim(0)
            except Exception as e:
                logger.warning(f"Failed to trim server memory: {e}")
        # =====================================================================

        logger.info(f"Total inference execution time: {time.time() - start_time:.2f} seconds")
        return data

    def inferer(self, data=None) -> SlidingWindowInferer:
        return SlidingWindowInferer(roi_size=[128, 128, 32], sw_batch_size=6, overlap=0.1)

    def pre_transforms(self, data=None) -> Sequence[Callable]:
        return []

    def post_transforms(self, data=None) -> Sequence[Callable]:
        return []