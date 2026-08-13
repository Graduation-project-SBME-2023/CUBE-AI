from __future__ import annotations

import logging
import os
from typing import Any, Dict, Mapping, Optional, Union

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
from storage_paths import NNUNET_DIR

from lib.infers.generic_segmentation import (
    GenericSegmentation as InferGenericSegmentation,
)
from lib.trainers.generic_segmentation import (
    GenericSegmentation as TrainGenericSegmentation,
)

logger = logging.getLogger(__name__)


class GenericSegmentation(TaskConfig):
    """Dynamic nnUNet model configuration created by a workflow request."""

    def __init__(
        self,
        labels: Mapping[str, int],
        model_name: str,
        plan_type: str,
        fold: int,
    ):
        super().__init__()
        self.epistemic_enabled = False
        self.epistemic_samples = 5
        self.root_nnunet_dir = os.getenv("nnUnet_folders", str(NNUNET_DIR))
        self.model_name = model_name
        self.plan_type = plan_type
        self.labels = dict(labels)
        self.fold = fold

    def init(
        self,
        name: str,
        model_dir: str,
        conf: Dict[str, str],
        planner: Any,
        **kwargs,
    ) -> None:
        super().init(name, model_dir, conf, planner, **kwargs)
        self.path = [
            os.path.join(self.model_dir, f"pretrained_{name}.pt"),
            os.path.join(self.model_dir, f"{name}.pt"),
        ]
        self.epistemic_enabled = bool(
            strtobool(conf.get("epistemic_enabled", "false"))
        )
        self.epistemic_samples = int(conf.get("epistemic_samples", "5"))
        logger.info(
            "Epistemic enabled: %s; samples: %s",
            self.epistemic_enabled,
            self.epistemic_samples,
        )

    def infer(self) -> Union[InferTask, Dict[str, InferTask]]:
        task = InferGenericSegmentation(
            path=self.path,
            labels=self.labels,
            model_name=self.model_name,
            fold=self.fold,
            plan_type=self.plan_type,
            root_nnunet_dir=self.root_nnunet_dir,
            preload=bool(strtobool(self.conf.get("preload", "false"))),
        )
        logger.info("Created generic inference task for %s", self.model_name)
        return task

    def trainer(self) -> Optional[TrainTask]:
        load_path = self.path[0] if os.path.exists(self.path[0]) else self.path[1]
        task = TrainGenericSegmentation(
            model_dir=self.model_dir,
            description="Train Generic Segmentation Model",
            load_path=load_path,
            disable_meta_tracking=False,
            publish_path=self.path[1],
            labels=self.labels,
            model_name=self.model_name,
            fold=self.fold,
            plan_type=self.plan_type,
            root_nnunet_dir=self.root_nnunet_dir,
        )
        logger.info("Created generic training task for %s", self.model_name)
        return task

    def strategy(self) -> Union[None, Strategy, Dict[str, Strategy]]:
        if not self.epistemic_enabled:
            return {}
        return {f"{self.name}_epistemic": Epistemic()}

    def scoring_method(
        self,
    ) -> Union[None, ScoringMethod, Dict[str, ScoringMethod]]:
        methods: Dict[str, ScoringMethod] = {
            "dice": Dice(),
            "sum": Sum(),
        }
        if self.epistemic_enabled:
            methods[f"{self.name}_epistemic"] = EpistemicScoring(
                model=self.path,
                network=UNet(
                    spatial_dims=3,
                    in_channels=1,
                    out_channels=1,
                    channels=[64, 128, 256, 512],
                    strides=[2, 2, 2],
                    num_res_units=4,
                    norm="Batch",
                    bias=False,
                    dropout=0.5,
                ),
                transforms=[],
                num_samples=self.epistemic_samples,
            )
        return methods
