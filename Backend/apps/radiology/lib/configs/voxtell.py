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
from monailabel.utils.others.generic import download_file, strtobool
# voxtell dependencies
import gc
import ctypes
import torch
import numpy as np
from typing import List, Union

# downloading dependencies
from huggingface_hub import snapshot_download

import urllib.request
from lib.configs.qwen_embedding_model import ensure_qwen_embedding_model
from lib.configs.voxtell_supported_labels import VOXTELL_SUPPORTED_LABELS

logger = logging.getLogger(__name__)

class voxtell(TaskConfig):
    
            
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
        self.supported_prompts = VOXTELL_SUPPORTED_LABELS
        self.model_name = "voxtell_v1.1"

        # Download model if not present
        self.model_path = os.path.join(self.model_dir, self.model_name)
        if not os.path.exists(self.model_path):
            logger.info(f"Downloading Voxtell model to {self.model_path}")
            self.__download_voxtell_checkpoints(self.model_name, self.model_dir)
        else:
            logger.info(f"Using existing model at {self.model_path}")
            
        # Downloading text encoding model 
        self.text_encoding_model_name = "qwen3-4b-embedding"
        self.text_encoding_model_path = os.path.join(self.model_dir, self.text_encoding_model_name)
        ensure_qwen_embedding_model(
            self.text_encoding_model_path,
            log=logger.info,
        )
        
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
        task: InferTask = lib.infers.voxtell(
        path=self.model_path,
        network=self.network,
        labels=self.labels,
        text_encoding_model_path=self.text_encoding_model_path,
        preload=strtobool(self.conf.get("preload", "false")),
    )
        return task
    
    def trainer(self) -> Optional[TrainTask]:
        output_dir = os.path.join(self.model_dir, self.name)
        load_path = self.model_path[0] if os.path.exists(self.model_path[0]) else self.model_path[1]

        task: TrainTask = lib.trainers.voxtell(
            model_dir=output_dir,
            network=self.network,
            # target_spacing=self.target_spacing,
            description="Train VoxTell Segmentation Model",
            load_path=load_path,
            publish_path=self.model_path[1],
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
                model=self.model_path,
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
                    transforms=lib.infers.voxtell(None).pre_transforms(),
                    num_samples=self.epistemic_samples,
                )
        return methods

    def __download_voxtell_checkpoints(self, model_name, download_dir):
        
        download_path = snapshot_download(
        repo_id="mrokuss/VoxTell",
        allow_patterns=[f"{model_name}/*", "*.json"],
        local_dir=download_dir
            )
