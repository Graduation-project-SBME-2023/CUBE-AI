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
import os
from typing import Any, Dict, Optional, Union
import torch

import lib.infers
import lib.trainers
from monai.networks.nets import UNet

from monailabel.interfaces.config import TaskConfig
from monailabel.interfaces.tasks.infer_v2 import InferTask
from monailabel.interfaces.tasks.scoring import ScoringMethod
from monailabel.interfaces.tasks.strategy import Strategy
from monailabel.interfaces.tasks.train import TrainTask
from monailabel.tasks.activelearning.epistemic import Epistemic
from monailabel.tasks.scoring.dice import Dice
from monailabel.tasks.scoring.epistemic import EpistemicScoring
from monailabel.tasks.scoring.sum import Sum
from monailabel.utils.others.generic import strtobool

import urllib.request

logger = logging.getLogger(__name__)

class sam(TaskConfig):
    
            
    def __init__(self):
        super().__init__()

        self.epistemic_enabled = None
        self.epistemic_samples = None

    def init(self, name: str, model_dir: str, conf: Dict[str, str], planner: Any, **kwargs):
        super().init(name, model_dir, conf, planner, **kwargs)

        # Labels
        self.labels = {
            "Seg": 1,
        }
        
        # Model Files
        # self.path = [
        #     os.path.join(self.model_dir, f"pretrained_{name}.pt"),  # pretrained
        #     os.path.join(self.model_dir, f"{name}.pt"),  # published
        # ]

        # Download model if not present
        self.path = os.path.join(self.model_dir, "sam2.1_hiera_large.pt")
        if not os.path.exists(self.path):
            logger.info(f"Downloading model to {self.path}")
            self.__download_checkpoints()
        else:
            logger.info(f"Using existing model at {self.path}")
        
        # Network
        self.network = UNet(
                    spatial_dims = 3,
                    in_channels = 1,
                    out_channels = 1,
                    channels = [64, 128, 256, 512],
                    strides = [2, 2, 2],
                    num_res_units = 8,
                    norm = "BATCH",
                    bias = False
                    )        
        
        
        # Others
        self.epistemic_enabled = strtobool(conf.get("epistemic_enabled", "false"))
        self.epistemic_samples = int(conf.get("epistemic_samples", "5"))
        # logger.info(f"EPISTEMIC Enabled: {self.epistemic_enabled}; Samples: {self.epistemic_samples}")
        
    def infer(self) -> Union[InferTask, Dict[str, InferTask]]:
        task: InferTask = lib.infers.sam(
        path=self.path,
        network=self.network,
        labels=self.labels,
        preload=strtobool(self.conf.get("preload", "false")),
    )
        return task
    
    def trainer(self) -> Optional[TrainTask]:
        output_dir = os.path.join(self.model_dir, self.name)
        load_path = self.path[0] if os.path.exists(self.path[0]) else self.path[1]

        task: TrainTask = lib.trainers.sam(
            model_dir=output_dir,
            network=self.network,
            # target_spacing=self.target_spacing,
            description="Train Neurobalstoma Segmentation Model",
            load_path=load_path,
            publish_path=self.path[1],
            labels=self.labels,
            disable_meta_tracking=False,
        )
        return task
    
    def strategy(self) -> Union[None, Strategy, Dict[str, Strategy]]:
        strategies: Dict[str, Strategy] = {}
        if self.epistemic_enabled:
            strategies[f"{self.name}_epistemic"] = Epistemic()
        return strategies
    
    def scoring_method(self) -> Union[None, ScoringMethod, Dict[str, ScoringMethod]]:
        methods: Dict[str, ScoringMethod] = {
            "dice": Dice(),
            "sum": Sum(),
        }
        
        if self.epistemic_enabled:
            methods[f"{self.name}_epistemic"] = EpistemicScoring(
                model=self.path,
                network=UNet(
                        spatial_dims = 3,
                        in_channels = 1,
                        out_channels = 1,
                        channels = [64, 128, 256, 512],
                        strides = [2, 2, 2],
                        num_res_units = 4,
                        norm = "Batch",
                        bias = False,
                        dropout = 0.5
                        ),
                    transforms=lib.infers.sam(None).pre_transforms(),
                    num_samples=self.epistemic_samples,
                )
        return methods

    def __download_checkpoints(self):
        download_url = "https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_large.pt"
        try:
            urllib.request.urlretrieve(download_url, self.path)
        except Exception as e:
            raise RuntimeError(f"Failed to download checkpoint: {e}")
  
