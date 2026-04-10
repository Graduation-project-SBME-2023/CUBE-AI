import gc
import ctypes
import torch
import numpy as np
from typing import List, Union
from transformers import AutoModel, AutoTokenizer
from optimum.quanto import quantize, freeze, qint4

from voxtell.inference.predictor import VoxTellPredictor
from voxtell.utils.text_embedding import last_token_pool, wrap_with_instruction
from acvl_utils.cropping_and_padding.bounding_boxes import insert_crop_into_image
from nnunetv2.utilities.helpers import empty_cache


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

        # Free 0.6B loaded by parent immediately
        del self.text_backbone
        gc.collect()
        torch.cuda.empty_cache()
        import platform
        if platform.system() == "Linux":
            ctypes.cdll.LoadLibrary("libc.so.6").malloc_trim(0)

        print("Loading tokenizer...")
        self.tokenizer = AutoTokenizer.from_pretrained(
            text_encoding_model,
            padding_side='left'
        )

        print("Loading Qwen3-4B in fp16 on CPU...")
        self.text_backbone = AutoModel.from_pretrained(
            text_encoding_model,
            device_map="cpu",
            low_cpu_mem_usage=True,
            dtype=torch.float16,  # fp16 = ~4GB peak RAM instead of ~16GB
        )

        print("Quantizing to 4-bit...")
        quantize(self.text_backbone, weights=qint4)
        freeze(self.text_backbone)
        self.text_backbone.eval()
        print("Qwen3-4B loaded in 4-bit on CPU ✅")

    def predict_single_image(
        self,
        data: np.ndarray,
        text_prompts: Union[str, List[str]]
    ) -> np.ndarray:
        """
        Predict segmentation masks for a single image with text prompts.

        Args:
            data: Image data in RAS orientation (3D or 4D with channel dimension).
            text_prompts: Single text prompt or list of text prompts describing
                anatomical structures to segment.

        Returns:
            Segmentation masks as numpy array of shape (num_prompts, X, Y, Z)
            with binary values (0 or 1) indicating the segmented regions.
        """
        # Preprocess image
        data, bbox, orig_shape = self.preprocess(data)
        print(f"Preprocessed shape: {data.shape}")

        # Embed text prompts on CPU → returns tensor on GPU
        embeddings = self.embed_text_prompts(text_prompts)
        print(f"Embeddings shape: {embeddings.shape}")

        # Sliding window inference on GPU
        prediction = self.predict_sliding_window_return_logits(
            data, embeddings
        ).to("cpu")

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
        return segmentation_reverted_cropping

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
    
        # Cast to fp32 to match VoxTell's project_text_embed layer ✅
        embeddings = embeddings.float()
        embeddings = embeddings.to(self.device)
        embeddings = embeddings.view(1, n_prompts, -1)
    
        empty_cache(self.device)
        return embeddings
    
MODEL_NAME = "voxtell_v1.1"
download_path = r"D:\GP_2026\Github\agenticsystem\OHIF-Standalone\Backend" 
model_path = f"{download_path}/{MODEL_NAME}"
device = torch.device("cpu")
text_prompts = ["spleen"]
image_path =  r"C:\Users\Lenovo\Downloads\Compressed\1.2.826.0.1.3680043.8.498.15136386566608168882487015520907740870.nii.gz"
output_path = r"D:\GP_2026\Github\agenticsystem\OHIF-Standalone\Backend\apps\radiology\Labels\seg.nii.gz"

vox_predictor = MyVoxTellPredictor(model_dir=model_path, device=torch.device("cpu"),text_encoding_model=r"D:\GP_2026\Github\agenticsystem\OHIF-Standalone\Backend\models\qwen3-4b-embedding")
import SimpleITK as sitk
img = sitk.ReadImage(image_path)

orig_orient = sitk.DICOMOrientImageFilter_GetOrientationFromDirectionCosines(
    img.GetDirection()
)
print(f"Original orientation: {orig_orient}")
img_ras = sitk.DICOMOrient(img, "RAS")

img_np = sitk.GetArrayFromImage(img_ras)[None]
print(f"Image shape (C, D, H, W): {img_np.shape}")

voxtell_seg_np_ras = vox_predictor.predict_single_image(img_np, text_prompts[0])
print(f"Segmentation shape (C, D, H, W): {voxtell_seg_np_ras.shape}")
seg_ras = sitk.GetImageFromArray(voxtell_seg_np_ras[0])
seg_ras.CopyInformation(img_ras)

seg_final = sitk.DICOMOrient(seg_ras, orig_orient)
print(f"Final segmentation orientation: {sitk.DICOMOrientImageFilter_GetOrientationFromDirectionCosines(seg_final.GetDirection())}")
sitk.WriteImage(seg_final, output_path)
print("Segmentation saved successfully.")