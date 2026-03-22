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
import logging
import os
import nibabel as nib
import numpy as np
from enum import Enum
from typing import Callable, Sequence
from PIL import Image
import shutil
import gc
import time
import tempfile
import pickle
import ctypes
from typing import List, Union
from transformers import AutoModel, AutoTokenizer
from optimum.quanto import quantize, freeze, qint4

from voxtell.inference.predictor import VoxTellPredictor
from voxtell.utils.text_embedding import last_token_pool, wrap_with_instruction
from acvl_utils.cropping_and_padding.bounding_boxes import insert_crop_into_image
from nnunetv2.utilities.helpers import empty_cache
import SimpleITK as sitk
from monai.inferers import Inferer, SlidingWindowInferer, SliceInferer
from monailabel.interfaces.tasks.infer_v2 import InferType
from monailabel.tasks.infer.basic_infer import BasicInferTask


logger = logging.getLogger(__name__)


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
        text_encoding_model: str = "./models/qwen3-4b-embedding"  # local path after snapshot_download
    ):
        # Parent loads 0.6B (small, safe) to initialize seg model
        super().__init__(
            model_dir=model_dir,
            device=device,
            text_encoding_model="Qwen/Qwen3-Embedding-0.6B"
        )
        self.perform_everything_on_device = False

        # # Free 0.6B loaded by parent immediately
        # del self.text_backbone
        # gc.collect()
        # torch.cuda.empty_cache()
        # import platform
        # if platform.system() == "Linux":
        #     ctypes.cdll.LoadLibrary("libc.so.6").malloc_trim(0)

        # logger.info("Loading tokenizer...")
        # self.tokenizer = AutoTokenizer.from_pretrained(
        #     text_encoding_model,
        #     padding_side='left'
        # )

        # logger.info("Loading Qwen3-4B in fp16 on CPU...")
        # self.text_backbone = AutoModel.from_pretrained(
        #     text_encoding_model,
        #     device_map="cpu",
        #     low_cpu_mem_usage=True,
        #     dtype=torch.float16,  # fp16 = ~4GB peak RAM instead of ~16GB
        # )

        # logger.info("Quantizing to 4-bit...")
        # quantize(self.text_backbone, weights=qint4)
        # freeze(self.text_backbone)
        # self.text_backbone.eval()
        # logger.info("Qwen3-4B loaded in 4-bit on CPU ")

    # def predict_single_image(
    #     self,
    #     data: np.ndarray,
    #     text_prompts: Union[str, List[str]]
    # ) -> np.ndarray:
    #     """
    #     Predict segmentation masks for a single image with text prompts.

    #     Args:
    #         data: Image data in RAS orientation (3D or 4D with channel dimension).
    #         text_prompts: Single text prompt or list of text prompts describing
    #             anatomical structures to segment.

    #     Returns:
    #         Segmentation masks as numpy array of shape (num_prompts, X, Y, Z)
    #         with binary values (0 or 1) indicating the segmented regions.
    #     """
    #     # Preprocess image
    #     data, bbox, orig_shape = self.preprocess(data)
    #     logger.info(f"Preprocessed shape: {data.shape}")

    #     # Embed text prompts on CPU → returns tensor on GPU
    #     embeddings = self.embed_text_prompts(text_prompts)
    #     logger.info(f"Embeddings shape: {embeddings.shape}")
    #     # Sliding window inference on GPU
    #     prediction = self.predict_sliding_window_return_logits(
    #         data, embeddings
    #     ).to("cpu")

    #     # Postprocess logits → binary masks
    #     with torch.no_grad():
    #         prediction = torch.sigmoid(prediction.float()) > 0.5

    #     segmentation_reverted_cropping = np.zeros(
    #         [prediction.shape[0], *orig_shape],
    #         dtype=np.uint8
    #     )
    #     segmentation_reverted_cropping = insert_crop_into_image(
    #         segmentation_reverted_cropping, prediction, bbox
    #     )
    #     return segmentation_reverted_cropping
    def predict_single_image(
        self,
        data: np.ndarray,
        text_prompts: Union[str, List[str]]
    ) -> np.ndarray:
        """
        Dummy predictor that returns a small cube of 1s inside a zero volume.
        Useful for testing pipeline without running VoxTell inference.
        """

        # Ensure prompts are list
        if isinstance(text_prompts, str):
            text_prompts = [text_prompts]

        n_prompts = len(text_prompts)

        # Preprocess to get original shape + bbox
        data, bbox, orig_shape = self.preprocess(data)
        logger.info(f"Preprocessed shape: {data.shape}")
        logger.info(f"Original shape: {orig_shape}")

        # Create empty segmentation volume
        segmentation = np.zeros(
            (n_prompts, *orig_shape),
            dtype=np.uint8
        )

        # Define a small cube region in the center
        cx, cy, cz = (
            orig_shape[0] // 2,
            orig_shape[1] // 2,
            orig_shape[2] // 2
        )

        cube_size = 5  # adjust size if needed

        segmentation[
            :,
            cx-cube_size:cx+cube_size,
            cy-cube_size:cy+cube_size,
            cz-cube_size:cz+cube_size
        ] = 1

        logger.info("Dummy segmentation generated")

        return segmentation    

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
    
        with torch.no_grad():
            text_embed = self.text_backbone(**text_tokens)
            embeddings = last_token_pool(
                text_embed.last_hidden_state,
                text_tokens["attention_mask"]
            )
    
        # Cast to fp32 to match VoxTell's project_text_embed layer 
        embeddings = embeddings.float()
        embeddings = embeddings.to(self.device)
        embeddings = embeddings.view(1, n_prompts, -1)
    
        empty_cache(self.device)
        return embeddings
  

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



    def initialize_predictor(self, device=torch.device("cpu")):
        if self.voxtell_predictor is None:
            self.voxtell_predictor = MyVoxTellPredictor(model_dir=self.model_path, device=device, text_encoding_model=self.text_encoding_model_path)

    def run_inferer(self, data, device="cuda"):
        """
        Run Inferer over pre-processed Data.
        """
        start_time = time.time()
       
        # Load the 3D image in RAS orientation
        image_array, image_ras, orig_orient = ImageProcessor.load_image_in_ras(data["image"])
        # Initialize the predictor for new task
        self.initialize_predictor()
        # Get text prompts from data
        text_prompts = data.get("text_prompts", "spleen") 
        # Predict segmentation masks
        voxtell_seg_np_ras = self.voxtell_predictor.predict_single_image(image_array, text_prompts)
        # Convert segmentation back to original orientation
        seg_final = ImageProcessor.convert_seg_to_orginal_orientation(voxtell_seg_np_ras, image_ras, orig_orient)
        
        # save results
        data["pred"] = seg_final
        logger.info(f"Execution time: {time.time() - start_time:.2f} seconds")
        return data


    def inferer(self, data=None) -> SlidingWindowInferer:
        return SlidingWindowInferer(roi_size=[128, 128, 32], sw_batch_size=6, overlap=0.1)

    def pre_transforms(self, data=None) -> Sequence[Callable]:
        return []

    def post_transforms(self, data=None) -> Sequence[Callable]:
        return []
