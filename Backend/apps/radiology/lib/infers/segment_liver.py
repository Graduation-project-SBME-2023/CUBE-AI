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

from typing import Callable, Sequence

from monai.inferers import Inferer, SlidingWindowInferer
from monai.transforms import (
    Activationsd,
    AsDiscreted,
    EnsureChannelFirstd,
    EnsureTyped,
    KeepLargestConnectedComponentd,
    LoadImaged,
    Orientationd,
    ScaleIntensityd,
    ScaleIntensityRanged,
    Spacingd,
    ForegroundMaskD,
    FillHolesD,
)

from monailabel.interfaces.tasks.infer_v2 import InferType
from monailabel.tasks.infer.basic_infer import BasicInferTask
from monailabel.transform.post import Restored


class segmentliver(BasicInferTask):
    """
    This provides Inference Engine for pre-trained liver segmentation (UNet) model over MSD Dataset.
    """

    def __init__(
        self,
        path,
        network=None,
        type=InferType.SEGMENTATION,
        labels=None,
        dimension=3,
        description="A pre-trained model for volumetric (3D) segmentation of the liver from CT image",
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

    def pre_transforms(self, data=None) -> Sequence[Callable]:
        return [
            LoadImaged(keys="image"),
            EnsureTyped(keys="image", device=data.get("device") if data else None),
            EnsureChannelFirstd(keys="image"),
            Orientationd(keys="image", axcodes="LAS"),
            ScaleIntensityRanged(
                keys="image", a_min=-135, a_max=215, b_min=0.0, b_max=1.0, clip=True
            ),
            # ForegroundMaskD(keys= "label", threshold=0.5, invert=True,allow_missing_keys=True),
        ]

    def inferer(self, data=None) -> Inferer:
        return SlidingWindowInferer(
            roi_size=[128, 128, 32],
            sw_batch_size=12,  # old value 12
            overlap=0.1,  # old value 0.5
        )

    def inverse_transforms(self, data=None):
        return []

    def post_transforms(self, data=None) -> Sequence[Callable]:
        device = data.get("device") if data else None

        print("1: Before EnsureTyped")
        ensure_type_transform = EnsureTyped(keys="pred", device=device)

        print("2: Before Activationsd")
        activations_transform = Activationsd(keys="pred", sigmoid=True)

        print("3: Before AsDiscreted")
        as_discrete_transform = AsDiscreted(keys="pred", threshold=0.5)

        print("4: Before KeepLargestConnectedComponentd")
        keep_largest_connected_component_transform = KeepLargestConnectedComponentd(
            keys="pred"
        )

        print("5: Before Restored")
        restore_transform = Restored(keys="pred", ref_image="image")

        print("6: Before FillHolesD")
        fill_holes_transform = FillHolesD(keys="pred")

        return [
            ensure_type_transform,
            activations_transform,
            as_discrete_transform,
            keep_largest_connected_component_transform,
            restore_transform,
            fill_holes_transform,
        ]
