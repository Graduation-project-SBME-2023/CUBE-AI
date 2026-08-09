
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
from storage_paths import MODELS_DIR
from lib.configs.vista_labels import VISTA_CLASS_MAP
from typing import Any, Dict, Optional, Union
import zipfile

import lib.infers
import lib.trainers
from monailabel.interfaces.config import TaskConfig
from monailabel.interfaces.tasks.infer_v2 import InferTask
from monailabel.interfaces.tasks.train import TrainTask
from monailabel.utils.others.generic import download_file, strtobool
from monai.networks.nets import UNet
from pathlib import Path
import gdown
from lib.configs.model_artifacts import ensure_model_archive

logger = logging.getLogger(__name__)


class VISTAPOINT3D(TaskConfig):
    def __init__(self):
        super().__init__()

    def init(self, name: str, model_dir: str, conf: Dict[str, str], planner: Any, **kwargs):
        super().init(name, model_dir, conf, planner, **kwargs)

        # Labels — imported from vista_labels.py (single source of truth)
        self.labels = VISTA_CLASS_MAP

        models_path = os.getenv("MODELS_DIR", str(MODELS_DIR))
        self.path = [os.path.join(models_path, "vista.pt")]



        # Google Drive file ID (extracted from the URL)
        file_id = "1L7rNfTIliBpT10Ty8p2GB2GbuQSBY9ib"
        url = f"https://drive.google.com/uc?id={file_id}"
        ensure_model_archive(
            self.path[0],
            lambda: gdown.download(url, self.path[0], quiet=False),
            description="VISTA checkpoint",
            log=logger.info,
        )


        self.target_spacing = (1.5, 1.5, 1.5)  # target space for image
        # Setting ROI size - This is for the image padding
        self.roi_size = (96, 96, 96)

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



    def download_checkpoint(self, zip_path, extract_to='.'):
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_to)
            logger.info(f"Extracted all files to: {os.path.abspath(extract_to)}")

   

    def infer(self) -> Union[InferTask, Dict[str, InferTask]]:
        task: InferTask = lib.infers.VISTAPOINT3D(
            path=self.path,
            network=self.network,
            labels=self.labels,
            preload=strtobool(self.conf.get("preload", "false")),
        )
        return task

    def trainer(self) -> Optional[TrainTask]:
        None
