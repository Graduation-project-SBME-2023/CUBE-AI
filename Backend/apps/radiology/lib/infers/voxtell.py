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
os.environ["TORCH_CUDA_ARCH_LIST"] = "8.0;8.6;8.9;9.0"
os.environ["QUANTO_DISABLE_CUDA"] = "1"
os.environ["CUDA_HOME"] = "/usr/local/cuda"
import torch
import nibabel as nib
import numpy as np
from enum import Enum
from typing import Callable, Sequence, List, Union
from PIL import Image
import shutil
import gc
import time
import tempfile
import pickle
import ctypes
from transformers import AutoModel, AutoTokenizer
from optimum.quanto import quantize, freeze, qint4
from transformers import BitsAndBytesConfig
from transformers import QuantoConfig
from unittest.mock import patch, MagicMock
from voxtell.inference.predictor import VoxTellPredictor
from voxtell.utils.text_embedding import last_token_pool, wrap_with_instruction
from acvl_utils.cropping_and_padding.bounding_boxes import insert_crop_into_image
from nnunetv2.utilities.helpers import empty_cache
import SimpleITK as sitk
import psutil
import logging
import sys
from voxtell.inference.predictor import VoxTellPredictor
from voxtell.utils.text_embedding import last_token_pool, wrap_with_instruction
from acvl_utils.cropping_and_padding.bounding_boxes import insert_crop_into_image
from nnunetv2.utilities.helpers import empty_cache
from monai.inferers import Inferer, SlidingWindowInferer, SliceInferer
from monailabel.interfaces.tasks.infer_v2 import InferType
from monailabel.tasks.infer.basic_infer import BasicInferTask


logger = logging.getLogger(__name__)

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
            text_encoding_model: str = None 
        ):
            # Bypass parent's HuggingFace loading
            with patch('transformers.AutoModel.from_pretrained') as mock_automodel, \
                            patch('transformers.AutoTokenizer.from_pretrained') as mock_tokenizer:
                            
                            mock_automodel.return_value = MagicMock()
                            mock_tokenizer.return_value = MagicMock()
                
            super().__init__(
                model_dir=model_dir,
                device=device,
                text_encoding_model="Qwen/Qwen3-Embedding-0.6B" # Ignored due to mock
            )
            self.perform_everything_on_device = False
            
            # 1. LOAD THE CACHED VECTORS INSTEAD OF THE TEXT MODEL
            cache_path = os.path.join(model_dir, "text_embeddings_cache.pkl")
            if os.path.exists(cache_path):
                with open(cache_path, "rb") as f:
                    self.embeddings_cache = pickle.load(f)
                logger.info(f"Loaded cached embeddings for: {list(self.embeddings_cache.keys())}")
            else:
                logger.error(f"Cache file NOT FOUND at {cache_path}! Run generate_cache.py first.")
                self.embeddings_cache = {}

            # 2. MOVE VOXTELL DIRECTLY TO GPU
            # Since there is no text model, the GPU is empty and ready for VoxTell!
            self.network = self.network.to(self.device)
            torch.cuda.empty_cache()
            check_memory("After INIT (VoxTell loaded, NO text model in RAM)", torch.device("cuda"))

    def embed_text_prompts(
        self,
        text_prompts: Union[List[str], str]
    ) -> torch.Tensor:
        """Instantly retrieves the pre-calculated mathematical vectors."""
        if isinstance(text_prompts, str):
            text_prompts = [text_prompts]
        
        embeddings_list = []
        for prompt in text_prompts:
            key = prompt.lower().strip()
            if key in self.embeddings_cache:
                embeddings_list.append(self.embeddings_cache[key])
            else:
                raise ValueError(f"Prompt '{key}' was not found in the cache! Add it to generate_cache.py")

        # Combine into a single tensor and push to GPU
        embeddings = torch.cat(embeddings_list, dim=1) 
        return embeddings.to(self.device)    


    def predict_single_image(
        self,
        data: np.ndarray,
        text_prompts: Union[str, List[str]]
    ) -> np.ndarray:
        """
        Hyper-Optimized Offline-Embedding Pipeline
        """
        # ==========================================================
        # PHASE 1: INSTANT TEXT EMBEDDING 
        # ==========================================================
        logger.info(f"Phase 1: Retrieving cached text embeddings for {text_prompts}...")
        embeddings = self.embed_text_prompts(text_prompts)
        check_memory("AFTER extracting the embedding", torch.device("cuda"))

        # ==========================================================
        # PHASE 2: IMAGE PREPROCESSING 
        # ==========================================================
        logger.info("Phase 2: Preprocessing 3D Medical Image...")
        data, bbox, orig_shape = self.preprocess(data)
        logger.info(f"Preprocessed shape: {data.shape}")
        check_memory("AFTER preporcessing the data", torch.device("cuda"))
        
        # ==========================================================
        # PHASE 3: SLIDING WINDOW INFERENCE
        # ==========================================================
        logger.info("Phase 3: Running sliding window inference...")
        prediction = self.predict_sliding_window_return_logits(
            data, embeddings
        ).to("cpu")
        check_memory("AFTER SLIDING WINDOW INFERENCE", torch.device("cuda"))
        
        # Postprocess logits → binary masks
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
            self.voxtell_predictor = None
            self.model_path = path
            self.text_encoding_model_path = text_encoding_model_path

    def initialize_predictor(self, device=torch.device("cuda")):
        """Initializes the predictor strictly ONCE to keep VRAM consumption stable across requests."""
        if self.voxtell_predictor is None:
            logger.info("Initializing MyVoxTellPredictor for the first time...")
            self.voxtell_predictor = MyVoxTellPredictor(
                model_dir=self.model_path, 
                device=device, 
                text_encoding_model=self.text_encoding_model_path
            )
        else:
            logger.info("Predictor already cached in memory. Skipping initialization.")

    def run_inferer(self, data, device="cuda"):
            """
            Run Inferer over pre-processed Data.
            """
            start_time = time.time()
        
            # 1. Load the 3D image
            image_array, image_ras, orig_orient = ImageProcessor.load_image_in_ras(data["image"])
            
            # 2. Initialize the predictor (Only hits disk on the very first API request)
            self.initialize_predictor(device=torch.device(device))
            
            # 3. Retrieve target prompt from MONAILabel client parameters or fallback
            # Safe parsing: looks for 'params -> label' (standard MONAILabel UI parameter) or text_prompts
            params = data.get("params", {})
            text_prompts = params.get("label", data.get("text_prompts", "spleen"))
            
            # 4. Run inference
            voxtell_seg_np_ras = self.voxtell_predictor.predict_single_image(image_array, text_prompts)
            
            # 5. Convert orientation back to original DICOM space
            seg_final = ImageProcessor.convert_seg_to_orginal_orientation(voxtell_seg_np_ras, image_ras, orig_orient)
            seg_np = sitk.GetArrayFromImage(seg_final)  
            seg_np = seg_np.transpose(2, 1, 0)       
            seg_np = seg_np[None].astype(np.uint8)              
            data["pred"] = torch.from_numpy(seg_np) 

            # =====================================================================
            # POST-REQUEST CLEANUP (Cleans activations, keeps the model loaded)
            # =====================================================================
            logger.info("Performing post-inference memory optimization...")
            
            # NOTICE: We DO NOT set self.voxtell_predictor = None anymore!
            # The model stays warm inside the GPU memory ready for the next request.
            
            gc.collect()
            torch.cuda.empty_cache()
            
            import platform
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