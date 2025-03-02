import logging
import os
from typing import Any, Dict, Optional, Union

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
from pathlib import Path

logger = logging.getLogger(__name__)
class GenericSegmentation(TaskConfig):
    def __init__(self,labels:str, 
                 model_name:str ,
                 plan_type:str, 
                 fold:int 
                 ):
        
        super().__init__()

        self.epistemic_enabled = None
        self.epistemic_samples = None

        # Dynamically set the root directory (go up two levels from the script's location)

        BASE_DIR = Path(__file__).resolve().parents[2]    
        self.root_nnunet_dir = os.path.join(BASE_DIR, "nnunet")
        
        self.model_name=model_name
        self.plan_type=plan_type
        self.labels = labels
        self.fold = fold
    

    def init(
        self, name: str, model_dir: str, conf: Dict[str, str], planner: Any, **kwargs
    ):
        super().init(name, model_dir, conf, planner, **kwargs)

        # Model Files
        self.path = [   
            os.path.join(self.model_dir, f"pretrained_{name}.pt"),  # pretrained
            os.path.join(self.model_dir, f"{name}.pt"),  # published
        ]

        # Others
        self.epistemic_enabled = strtobool(conf.get("epistemic_enabled", "false"))
        self.epistemic_samples = int(conf.get("epistemic_samples", "5"))
        logger.info(f"EPISTEMIC Enabled: {{self.epistemic_enabled}}; Samples: {{self.epistemic_samples}}")

    def infer(self) -> Union[InferTask, Dict[str, InferTask]]:
        task: InferTask = lib.infers.GenericSegmentation(
            path=self.path,
            labels=self.labels,
            model_name=self.model_name,
            fold=self.fold,
            plan_type=self.plan_type,
            root_nnunet_dir=self.root_nnunet_dir,
            preload=strtobool(self.conf.get("preload", "false")),
        )
        return task

    def trainer(self) -> Optional[TrainTask]:
        output_dir = os.path.join(self.model_dir, self.name)
        load_path = self.path[0] if os.path.exists(self.path[0]) else self.path[1]

        task: TrainTask = lib.trainers.GenericSegmentation(
            model_dir=self.model_dir,
            # target_spacing=self.target_spacing,
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
        return task
        
    def strategy(self) -> Union[None, Strategy, Dict[str, Strategy]]:
        strategies: Dict[str, Strategy] = {{}}
        if self.epistemic_enabled:
            strategies[f"{{self.name}}_epistemic"] = Epistemic()
        return strategies

    def scoring_method(self) -> Union[None, ScoringMethod, Dict[str, ScoringMethod]]:
        methods: Dict[str, ScoringMethod] = {{
            "dice": Dice(),
            "sum": Sum(),
        }}

        if self.epistemic_enabled:
            methods[f"{{self.name}}_epistemic"] = EpistemicScoring(
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
                transforms=lib.infers.GenericSegmentation(None).pre_transforms(),
                num_samples=self.epistemic_samples,
            )
        return methods