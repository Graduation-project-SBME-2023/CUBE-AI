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

import torch
import os
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
            text_encoding_model: str = "./models/qwen3-4b-embedding"
        ):
            with patch('transformers.AutoModel.from_pretrained') as mock_automodel:
                mock_automodel.return_value = MagicMock()
                
            super().__init__(
                model_dir=model_dir,
                device=device,
                text_encoding_model="Qwen/Qwen3-Embedding-0.6B"
            )
            self.perform_everything_on_device = False
            check_memory("direct after super init", torch.device("cuda"))
            # CRITICAL: Force VoxTell to CPU so the GPU is 100% empty!
            self.network = self.network.to("cpu")
            torch.cuda.empty_cache()
            check_memory("After moving the network to cpu", torch.device("cuda"))
            logger.info("Phase 1: Loading Tokenizer and Text Model directly to GPU VRAM...")
            
            self.tokenizer = AutoTokenizer.from_pretrained(
                text_encoding_model, 
                padding_side='left',
                local_files_only=True
            )
            check_memory("AFTER loading the tokenizer", torch.device("cuda"))
            q_config = QuantoConfig(weights="int4")
            self.text_backbone = AutoModel.from_pretrained(
                text_encoding_model,
                device_map="cuda", # Streams directly to GPU
                low_cpu_mem_usage=True,
                quantization_config=q_config,
                local_files_only=True
            )
            self.text_backbone.eval()
            check_memory("AFTER loading the backbone", torch.device("cuda"))
    def embed_text_prompts(
        self,
        text_prompts: Union[List[str], str]
    ) -> torch.Tensor:
        if isinstance(text_prompts, str):
            text_prompts = [text_prompts]
        n_prompts = len(text_prompts)
    
        text_prompts_wrapped = wrap_with_instruction(text_prompts)
    
        text_tokens = self.tokenizer(
            text_prompts_wrapped,
            padding=True,
            truncation=True,
            max_length=self.max_text_length,
            return_tensors="pt",
        )
        
        # CRITICAL: Move tokens to GPU so they can interact with the GPU text model
        text_tokens = {k: v.to(self.device) for k, v in text_tokens.items()}
    
        with torch.no_grad():
            text_embed = self.text_backbone(**text_tokens)
            embeddings = last_token_pool(
                text_embed.last_hidden_state,
                text_tokens["attention_mask"]
            )
    
        # Cast to fp32 to match VoxTell's precision
        embeddings = embeddings.float()
        embeddings = embeddings.view(1, n_prompts, -1)
        
        # Vector is already on self.device (cuda), ready to be returned
        return embeddings    


    def predict_single_image(
        self,
        data: np.ndarray,
        text_prompts: Union[str, List[str]]
    ) -> np.ndarray:
        """
        Optimized execution order to prevent 8GB System RAM bottleneck.
        """
        # ==========================================================
        # PHASE 1: TEXT EMBEDDING (Uses Text model currently on GPU)
        # ==========================================================
        logger.info("Phase 1: Embedding text prompts...")
        embeddings = self.embed_text_prompts(text_prompts)
        logger.info(f"Embeddings successfully created. Shape: {embeddings.shape}")
        check_memory("AFTER extracting the embedding", torch.device("cuda"))
        # ==========================================================
        # PHASE 2: TOTAL TEXT PURGE (Free the GPU for VoxTell)
        # ==========================================================
        logger.info("Phase 2: Purging Text Encoder from VRAM and System RAM...")
        del self.text_backbone
        del self.tokenizer
        gc.collect()
        
        # CRITICAL: You MUST empty cache so the GPU actually drops the 2.5GB 
        torch.cuda.empty_cache()
        
        import platform
        if platform.system() == "Linux":
            import ctypes
            try:
                ctypes.cdll.LoadLibrary("libc.so.6").malloc_trim(0)
            except Exception as e:
                pass
                
        # VRAM should now drop down to ~0.00 GB
        check_memory("AFTER TEXT PURGE", torch.device("cuda"))

        # ==========================================================
        # PHASE 3: LOAD VOXTELL NETWORK TO GPU
        # ==========================================================
        logger.info("Phase 3: Moving VoxTell neural network to GPU VRAM...")
        self.network = self.network.to(self.device)
        
        # VRAM will show ~1.65 GB, System RAM will remain safe
        check_memory("AFTER LOADING VOXTELL", torch.device("cuda"))

        # ==========================================================
        # PHASE 4: IMAGE PREPROCESSING 
        # ==========================================================
        logger.info("Phase 4: Preprocessing 3D Medical Image...")
        data, bbox, orig_shape = self.preprocess(data)
        logger.info(f"Preprocessed shape: {data.shape}")
        check_memory("AFTER preporcessing the data", torch.device("cuda"))
        # ==========================================================
        # PHASE 5: SLIDING WINDOW INFERENCE
        # ==========================================================
        logger.info("Phase 5: Running sliding window inference...")
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
    """Implements Voxtell-based segmentation with improved structure."""

    def __init__(
            self,
            path,
            text_encoding_model_path,
            network=None,
            type=InferType.SEGMENTATION,
            labels=None,
            dimension=3,
            description=" Pretrained Voxtell Model for video segmentation ",
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
            self.annotated_image = None # may help in session handling keep the segmentation 
            self.image_path = None
            self.voxtell_predictor = None
            self.model_path = path
            self.text_encoding_model_path = text_encoding_model_path



    def initialize_predictor(self, device=torch.device("cuda")):
        if self.voxtell_predictor is None:
            self.voxtell_predictor = MyVoxTellPredictor(model_dir=self.model_path, device=device, text_encoding_model=self.text_encoding_model_path)



    def initialize_predictor(self, device=torch.device("cuda")):
        if self.voxtell_predictor is None:
            self.voxtell_predictor = MyVoxTellPredictor(model_dir=self.model_path, device=device, text_encoding_model=self.text_encoding_model_path)

    def run_inferer(self, data, device="cuda"):
            """
            Run Inferer over pre-processed Data.
            """
            start_time = time.time()
        
            # 1. Load the 3D image
            image_array, image_ras, orig_orient = ImageProcessor.load_image_in_ras(data["image"])
            
            # 2. Initialize the predictor (Loads Text Backbone to RAM, VoxTell to VRAM)
            self.initialize_predictor(device=torch.device(device))
            
            # 3. Predict segmentation masks (Deletes text backbone internally after use)
            text_prompts = data.get("text_prompts", "spleen") 
            voxtell_seg_np_ras = self.voxtell_predictor.predict_single_image(image_array, text_prompts)
            
            # 4. Convert orientation back
            seg_final = ImageProcessor.convert_seg_to_orginal_orientation(voxtell_seg_np_ras, image_ras, orig_orient)
            seg_np = sitk.GetArrayFromImage(seg_final)         
            seg_np = seg_np[None].astype(np.uint8)              
            data["pred"] = torch.from_numpy(seg_np) 

            # =====================================================================
            # SERVER CLEANUP: WIPE EVERYTHING TO RESET RAM FOR THE NEXT REQUEST
            # =====================================================================
            logger.info("Performing end-of-inference server RAM/VRAM wipe...")
            
            # Completely destroy the predictor object
            self.voxtell_predictor = None 
            
            # Clear Python garbage collection and PyTorch VRAM cache
            gc.collect()
            torch.cuda.empty_cache()
            
            # Force Linux to reclaim all freed system RAM immediately
            import platform
            if platform.system() == "Linux":
                import ctypes
                try:
                    ctypes.cdll.LoadLibrary("libc.so.6").malloc_trim(0)
                    logger.info("Server RAM completely cleared back to baseline.")
                except Exception as e:
                    logger.warning(f"Failed to trim server memory: {e}")
            # =====================================================================

            logger.info(f"Execution time: {time.time() - start_time:.2f} seconds")
            return data
    def inferer(self, data=None) -> SlidingWindowInferer:
        return SlidingWindowInferer(roi_size=[128, 128, 32], sw_batch_size=6, overlap=0.1)

    def pre_transforms(self, data=None) -> Sequence[Callable]:
        return []

    def post_transforms(self, data=None) -> Sequence[Callable]:
        return []
