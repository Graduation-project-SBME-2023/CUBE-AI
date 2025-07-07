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

import torch
from monai.apps.deepedit.transforms import NormalizeLabelsInDatasetd
from monai.inferers import SlidingWindowInferer
from monai.losses import DiceLoss
from monai.transforms import (
    Activationsd,
    AsDiscreted,
    EnsureChannelFirstd,
    EnsureTyped,
    LoadImaged,
    Orientationd,
    RandSpatialCropd,
    ScaleIntensityd,
    ScaleIntensityRanged,
    SelectItemsd,
    Spacingd,
    ToTensord,
    FillHolesD,
    ForegroundMaskD,


)

from monailabel.tasks.train.basic_train import BasicTrainTask, Context

logger = logging.getLogger(__name__)


class segmentliver(BasicTrainTask):
    def __init__(
        self,
        model_dir,
        network,
        roi_size=(128, 128, 32),
        description="Train Segmentation model for liver",
        **kwargs,
    ):
        self._network = network
        self.roi_size = roi_size
        super().__init__(model_dir, description, **kwargs)

    def network(self, context: Context):
        return self._network

    def optimizer(self, context: Context):
        return torch.optim.Adam(context.network.parameters(), lr=1e-2)

    def loss_function(self, context: Context):
        return DiceLoss(sigmoid=True, jaccard=1, batch=True)

    def train_pre_transforms(self, context: Context):
        return [
            LoadImaged(keys=("image", "label")),
            EnsureChannelFirstd(keys=("image", "label")),
            Orientationd(keys=("image", "label"), axcodes="LAS"),
            ScaleIntensityRanged(keys="image", a_min=-135, a_max=125, b_min=0.0, b_max=1.0, clip=True),
            ForegroundMaskD(keys= "label", threshold=0.5, invert=True,allow_missing_keys=True),
            
        ]

    def train_post_transforms(self, context: Context):
        return [
            ToTensord(keys=("pred", "label")),
            Activationsd(keys="pred", sigmoid=True),
            AsDiscreted(
                keys=("pred", "label"),
                threshold=0.5,
            ),
        ]

    def val_pre_transforms(self, context: Context):
        return [
            LoadImaged(keys=("image", "label")),
            EnsureChannelFirstd(keys=("image", "label")),
            Orientationd(keys=("image", "label"), axcodes="LAS"),
            ScaleIntensityRanged(keys="image", a_min=-135, a_max=125, b_min=0.0, b_max=1.0, clip=True),
            ForegroundMaskD(keys= "label", threshold=0.5, invert=True,allow_missing_keys=True),
        ]

    def val_inferer(self, context: Context):
        return SlidingWindowInferer(roi_size=[128, 128, 32], sw_batch_size=8, overlap=0.5)
