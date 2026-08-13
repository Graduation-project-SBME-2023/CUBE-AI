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
from monai.inferers import SliceInferer
from monai.losses import DiceLoss
from monailabel.tasks.train.basic_train import BasicTrainTask, Context

logger = logging.getLogger(__name__)

class voxtell(BasicTrainTask):
    def __init__(
        self,
        model_dir,
        network,
        description="Train voxtell Segmentation model",
        **kwargs,
    ):
        self._network = network
        super().__init__(model_dir, description, **kwargs)


    def network(self, context: Context):
        return self._network

    def optimizer(self, context: Context):
        return torch.optim.Adam(context.network.parameters(), lr=0.01)

    # def scheduler(self, context: Context):
    #     return torch.optim.lr_scheduler.StepLR(self.optimizer, step_size=20, gamma=0.5, verbose=False)

    def loss_function(self, context: Context):
        return DiceLoss(batch=True, sigmoid=True, jaccard=1)


    def train_pre_transforms(self, context: Context):
        return []

    def train_post_transforms(self, context: Context):
        return []

    def val_pre_transforms(self, context: Context):
        return []


    def val_inferer(self, context: Context):
        return SliceInferer(roi_size=(256,256), spatial_dim = 0)
