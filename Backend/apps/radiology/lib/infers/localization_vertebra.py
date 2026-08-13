# Copyright (c) MONAI Consortium
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
from typing import Any, Callable, Sequence, Tuple, Union

import torch
from lib.transforms.transforms import CacheObjectd, VertebraLocalizationSegmentation
from monai.inferers import Inferer, SlidingWindowInferer
from monai.transforms import (
    Compose,
    CropForegroundd,
    EnsureChannelFirstd,
    EnsureTyped,
    GaussianSmoothd,
    KeepLargestConnectedComponentd,
    LoadImaged,
    Invertd,
    ScaleIntensityd,
    ScaleIntensityRanged,
    Spacingd,
)

from monailabel.interfaces.tasks.infer_v2 import InferType
from monailabel.tasks.infer.basic_infer import BasicInferTask
from monailabel.transform.post import Restored

logger = logging.getLogger(__name__)


class LocalizationVertebra(BasicInferTask):
    """
    This provides Inference Engine for pre-trained vertebra localization (UNet) model.
    """

    def __init__(
        self,
        path,
        network=None,
        target_spacing=(1.0, 1.0, 1.0),
        type=InferType.SEGMENTATION,
        labels=None,
        dimension=3,
        description="A pre-trained model for volumetric (3D) vertebra localization from CT image",
        **kwargs,
    ):
        super().__init__(
            path=path,
            network=network,
            type=type,
            labels=labels,
            dimension=dimension,
            description=description,
            load_strict=False,
            **kwargs,
        )
        self.target_spacing = target_spacing

    def pre_transforms(self, data=None) -> Sequence[Callable]:
        if data and isinstance(data.get("image"), str):
            t = [
                LoadImaged(keys="image", reader="ITKReader"),
                EnsureTyped(keys="image", device=data.get("device") if data else None),
                EnsureChannelFirstd(keys="image"),
                CacheObjectd(keys="image"),
                Spacingd(keys="image", pixdim=self.target_spacing, allow_missing_keys=True),
                ScaleIntensityRanged(keys="image", a_min=-1000, a_max=1900, b_min=0.0, b_max=1.0, clip=True),
                GaussianSmoothd(keys="image", sigma=0.4),
                ScaleIntensityd(keys="image", minv=-1.0, maxv=1.0),
            ]
        else:
            t = [
                EnsureChannelFirstd(keys="label"),
                CacheObjectd(keys="image"),
                Spacingd(keys=("image", "label"), pixdim=self.target_spacing),
                ScaleIntensityRanged(keys="image", a_min=-1000, a_max=1900, b_min=0.0, b_max=1.0, clip=True),
                GaussianSmoothd(keys="image", sigma=0.4),
                ScaleIntensityd(keys="image", minv=-1.0, maxv=1.0),
                CropForegroundd(keys=("image", "label"), source_key="label", margin=10),
            ]

        return t

    def inferer(self, data=None) -> Inferer:
        return SlidingWindowInferer(
            roi_size=self.roi_size,
            sw_batch_size=2,
            overlap=0.4,
            padding_mode="replicate",
            mode="gaussian",
            device=torch.device("cpu"),  # Otherwise a rather big GPU (>45GB) is needed
        )

    def inverse_transforms(self, data=None) -> Union[None, Sequence[Callable]]:
        # ``run_invert_transforms`` performs the inversion after reducing the
        # 25-channel logits to a single discrete label map.
        return []

    def run_invert_transforms(self, data, pre_transforms, names):
        """Restore a one-channel mask instead of 25 full-resolution logits.

        The default MONAI Label order restores every class channel before
        argmax. For a CT volume this can require gigabytes of RAM and minutes
        of CPU interpolation. Argmax is equivalent before softmax, and nearest
        interpolation preserves the resulting integer vertebra labels.
        """
        prediction = data[self.output_label_key]
        if prediction.ndim != 4:
            raise RuntimeError(
                "Vertebra localization expected CxDxHxW logits; "
                f"received shape {tuple(prediction.shape)}"
            )

        data[self.output_label_key] = torch.argmax(
            prediction,
            dim=0,
            keepdim=True,
        ).to(device="cpu", dtype=torch.float32)
        del prediction
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        logger.info(
            "Restoring one-channel vertebra mask with nearest interpolation"
        )
        inverter = Invertd(
            keys=self.output_label_key,
            transform=Compose(pre_transforms),
            orig_keys=self.input_key,
            nearest_interp=True,
            to_tensor=True,
            device="cpu",
        )
        return inverter(data)

    def post_transforms(self, data=None) -> Sequence[Callable]:
        return [
            # Prediction is already discretized before inverse resampling.
            EnsureTyped(keys="pred", device=torch.device("cpu")),
            KeepLargestConnectedComponentd(keys="pred"),
            Restored(keys="pred", ref_image="image_cached"),
            VertebraLocalizationSegmentation(keys="pred", result="result"),
        ]

    def writer(self, data, extension=None, dtype=None) -> Tuple[Any, Any]:
        if data.get("pipeline_mode", False):
            return {"image": data["image"], "pred": data["pred"]}, data["result"]

        return super().writer(data, extension, dtype)
