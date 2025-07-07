import os

# Define base templates for config, trainers, and infers
TEMPLATES = {
    "config": """# Config for {model_name}
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

logger = logging.getLogger(__name__)
class {class_name}(TaskConfig):
    def __init__(self):
        super().__init__()

        self.epistemic_enabled = None
        self.epistemic_samples = None

    def init(
        self, name: str, model_dir: str, conf: Dict[str, str], planner: Any, **kwargs
    ):
        super().init(name, model_dir, conf, planner, **kwargs)

        # Labels
        self.labels = {labels}

        # Model Files
        self.path = [   # changee
            os.path.join(self.model_dir, f"pretrained_{{name}}.pt"),  # pretrained
            os.path.join(self.model_dir, f"{{name}}.pt"),  # published
        ]

        self.task_name="{data_name}"



        # Network
        self.network = UNet(
            spatial_dims=3,
            in_channels=1,
            out_channels=2,
            channels=[16, 32, 64, 128, 256],
            strides=[2, 2, 2, 2],
            num_res_units=2,
            norm="batch",
        )    
        # Others
        self.epistemic_enabled = strtobool(conf.get("epistemic_enabled", "false"))
        self.epistemic_samples = int(conf.get("epistemic_samples", "5"))
        logger.info(f"EPISTEMIC Enabled: {{self.epistemic_enabled}}; Samples: {{self.epistemic_samples}}")

    def infer(self) -> Union[InferTask, Dict[str, InferTask]]:
        task: InferTask = lib.infers.{class_name}(
            path=self.path,
            network=self.network,
            labels=self.labels,
            preload=strtobool(self.conf.get("preload", "false")),
        )
        return task

    def trainer(self) -> Optional[TrainTask]:
        output_dir = os.path.join(self.model_dir, self.name)
        load_path = self.path[0] if os.path.exists(self.path[0]) else self.path[1]

        task: TrainTask = lib.trainers.{class_name}(
            model_dir=output_dir,
            # target_spacing=self.target_spacing,
            description="Train Neurobalstoma Segmentation Model",
            load_path=load_path,
            publish_path=self.path[1],
            labels=self.labels,
            disable_meta_tracking=False,
            task_name=self.task_name,
            fold=0,

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
                transforms=lib.infers.{class_name}(None).pre_transforms(),
                num_samples=self.epistemic_samples,
            )
        return methods



    


""",
    "trainer": """# Trainer for {model_name}
import os
import subprocess
import logging
from monailabel.tasks.train.basic_train import BasicTrainTask, Context
from nnunetv2.training.nnUNetTrainer.nnUNetTrainer import nnUNetTrainer
from nnunetv2.paths import nnUNet_preprocessed, nnUNet_results
# from nnunetv2.utilities.dataset_name_id_conversion import maybe_convert_to_dataset_name

logger = logging.getLogger(__name__)



class {class_name}(BasicTrainTask):
    def __init__(  # change
        self,
        model_dir,
        task_name,
        fold,
        trainer_class="nnUNetTrainer",
        description="Train and infer using nnUNet for segmentation",
        **kwargs,
    ):
        # Initialize the nnUNet Trainer Task.
        # :param model_dir: Path to the model directory
        # :param task_name: Name of the nnUNet task (e.g., TaskXXX_MyTask)
        # :param fold: Fold to train (default: 0)
        # :param trainer_class: Trainer class name in nnUNet
        # :param plans_file: Name of the nnUNet plans file
        self.task_name = task_name
        self.fold = fold
        self.trainer_class = trainer_class 


        task = "{task}"  # Replace with the actual task name or pass it as a parameter
        root_nnunet_dir=r"D:\\Liver Segmentation Meena 2024\\monai_radiology_app\\radiology\\nnunet"
        os.environ["nnUNet_raw"] = os.path.join(root_nnunet_dir, task, "nnUNet_raw")
        os.environ["nnUNet_preprocessed"] = os.path.join(root_nnunet_dir, task, "nnUNet_Preprocessed")
        os.environ["nnUNet_results"] = os.path.join(root_nnunet_dir, task, "nnUNet_Results")

        
        # Extract the numeric part from the task name
        task_id = ''.join(filter(str.isdigit, task_name))

        # Assign the extracted task ID to self.task_id
        self.task_id = task_id        
    
        super().__init__(model_dir, description, **kwargs)
    def network(self, context: Context):
        # nnUNet handles network creation internally, so this is not overridden.
        return None  # nnUNet internally initializes its network via the trainer.

    def optimizer(self, context: Context):
        # Optimizer is managed internally by nnUNet, so it is not overridden.
        return None  # nnUNet Trainer handles the optimizer.

    def loss_function(self, context: Context):
        # Loss function is managed internally by nnUNet, so it is not overridden.
        return None  # nnUNet Trainer handles the loss function.
    


 
     
    def preprocess_dataset(self):
        logger.info("Starting nnUNet Dataset Preprocessing")

        # Check if preprocessing is already done
        
        preprocessed_data_path = os.path.join(os.environ["nnUNet_preprocessed"], self.task_name)
        # if os.path.exists(preprocessed_data_path):
        #     logger.info(f"Dataset already preprocessed: {{preprocessed_data_path}}")
        #     return preprocessed_data_path
  

        # Build the nnUNet preprocessing command
        command = [    # change
            "nnUNetv2_plan_and_preprocess",
            "-d", self.task_id,
            "--verify_dataset_integrity"
        ]


        logger.info(f"Running command: {{' '.join(command)}}")

        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Preprocessing failed with error: {{e}}")
            raise RuntimeError(f"Preprocessing failed for dataset {{self.task_name}}","error: {{e}}")

        logger.info(f"Preprocessed data saved to: {{preprocessed_data_path}}")
        return preprocessed_data_path

    def train(self, rank: int, world_size: int, request, datalist):
        # Train the model using the nnUNet CLI.
        logger.info("Starting nnUNet Training")

        # Ensure preprocessing is complete
        self.preprocess_dataset()

        # Run the nnUNet training command
        # Build the nnUNet training command
        command = [      # change
            "nnUNetv2_train",
             self.task_id, 
            "2d",  
             str(self.fold), 
            "-tr", self.trainer_class, 
        ]
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Training failed with error: {{e}}")
            raise RuntimeError(f"Training failed for dataset {{self.task_name}}, fold {{self.fold}}, error: {{e}}")

        logger.info("nnUNet Training Completed")

    def val_inferer(self, context: Context):
        # Inference logic for nnUNet.
        from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor

        logger.info("Setting up nnUNet Predictor for Validation")

        # Initialize the nnUNet Predictor
        predictor = nnUNetPredictor(
            tile_step_size=0.5,  # Overlap size during sliding window inference
            device=context.device,
            verbose=True,
            verbose_preprocessing=True,
            allow_tqdm=True,
        )

        # Load the trained model
        predictor.initialize_from_trained_model_folder(
            os.path.join(nnUNet_results, self.task_name, self.trainer_class),
            use_folds=(self.fold,),
            checkpoint_name="checkpoint_best.pth",
        )

        return predictor

    def train_pre_transforms(self, context: Context):
        # nnUNet handles its own preprocessing. This is not needed.

        return None  # nnUNet handles preprocessing internally during training.

    def train_post_transforms(self, context: Context):
        # nnUNet handles its own postprocessing. This is not needed.
        return None  # nnUNet handles postprocessing internally.

    def val_pre_transforms(self, context: Context):
        # nnUNet handles its own validation preprocessing.
        return None  # nnUNet handles preprocessing internally during validation.

    def val_post_transforms(self, context: Context):
        # nnUNet handles its own validation postprocessing.
        return None  # nnUNet handles postprocessing internally


    
""",
    "infer": """# Infer for {model_name}
import torch
import logging
import copy
import os
import nibabel as nib
from enum import Enum
from typing import Any, Callable, Dict, Sequence, Tuple, Union


from monai.inferers import Inferer, SlidingWindowInferer, SliceInferer
from monailabel.interfaces.tasks.infer_v2 import InferType
from monailabel.tasks.infer.basic_infer import BasicInferTask
from monailabel.interfaces.utils.transform import dump_data
from monailabel.utils.others.generic import name_to_device


# should be changed to the actual folders paths:   Changeeee

# task = "{task}"  # Replace with the actual task name or pass it as a parameter
# root_nnunet_dir=r"C:\\Users\\Lenovo\\Desktop\\Grad\\Monai\\radiology\\nnunet"
# os.environ["nnUNet_raw"] = os.path.join(root_nnunet_dir, task, "nnUNet_raw")
# os.environ["nnUNet_preprocessed"] = os.path.join(root_nnunet_dir, task, "nnUNet_Preprocessed")
# os.environ["nnUNet_results"] = os.path.join(root_nnunet_dir, task, "nnUNet_Results")


from batchgenerators.utilities.file_and_folder_operations import join
from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor
from nnunetv2.paths import nnUNet_results


class {class_name}(BasicInferTask):
    def __init__(
        self,
        path,
        network=None,
        type=InferType.SEGMENTATION,
        labels=None,
        dimension=2,
        description="A pre-trained NnUnet model for volumetric segmentation ",
        **kwargs,
    ):
        super().__init__(
            path=path,
            network=network,
            type=type,
            labels=labels,
            dimension=dimension,
            description=description,
            load_strict=True,
            **kwargs,
        )

        task = "{task}"  # Replace with the actual task name or pass it as a parameter
        root_nnunet_dir=r"D:\\Liver Segmentation Meena 2024\\monai_radiology_app\\radiology\\nnunet"
        os.environ["nnUNet_raw"] = os.path.join(root_nnunet_dir, task, "nnUNet_raw")
        os.environ["nnUNet_preprocessed"] = os.path.join(root_nnunet_dir, task, "nnUNet_Preprocessed")
        os.environ["nnUNet_results"] = os.path.join(root_nnunet_dir, task, "nnUNet_Results")


    def run_inferer(self, data, convert_to_batch=True, device="cuda"):
        # Run Inferer over pre-processed Data.  Derive this logic to customize the normal behavior.
        # In some cases, you want to implement your own for running chained inferers over pre-processed data

        # :param data: pre-processed data
        # :param convert_to_batch: convert input to batched input
        # :param device: device type run load the model and run inferer
        # :return: updated data with output_key
        
        predictor = nnUNetPredictor(
            tile_step_size=0.5,  # 0.5 // old value
            device=torch.device("cuda"),
            verbose=True,
            verbose_preprocessing=True,
            allow_tqdm=True,
        )

        # Initializes the network architecture, loads the checkpoint
        predictor.initialize_from_trained_model_folder(    # Changee
            join(
                nnUNet_results,
                "{{task_name}}\\nnUNetTrainer__nnUNetPlans__3d_fullres",
            ),
            use_folds=(0,),
            checkpoint_name="checkpoint_best.pth",
        )

        seg = predictor.predict_from_files(
            [[data[self.input_key]]],
            self.temp_path,
            save_probabilities=False,
            overwrite=True,
            num_processes_preprocessing=4,  # worker
            num_processes_segmentation_export=4,  # worker
            folder_with_segs_from_prev_stage=None,
            num_parts=1,
            part_id=0,
        )

        if device.startswith("cuda"):
            torch.cuda.empty_cache()

        file_ending = ".nii.gz"
        basename = (
            os.path.basename(data[self.input_key])[: -(len(file_ending) + 5)]
            + file_ending
        )
        output_path = join(self.temp_path, basename)

        if os.path.exists(output_path):
            outputs = nib.load(output_path).get_fdata()
            outputs = torch.from_numpy(outputs)
            os.remove(output_path)

        data[self.output_label_key] = outputs

        return data

    def pre_transforms(self, data=None) -> Sequence[Callable]:
        return []

    def inferer(self, data=None) -> Inferer:
        return SlidingWindowInferer(
            roi_size=[128, 128, 32], sw_batch_size=6, overlap=0.1
        )

    def inverse_transforms(self, data=None):
        return []

    def post_transforms(self, data=None) -> Sequence[Callable]:
        return []


""",
}

# Function to generate files
def create_monai_files(class_name, model_name, model_path, labels,task,data_name,base_dir='radiology/lib'):
    # Directories for config, trainers, and infers
    directories = {
        "config": os.path.join(base_dir, "configs"),
        "trainer": os.path.join(base_dir, "trainers"),
        "infer": os.path.join(base_dir, "infers"),
    }

    # Create the directories if they don't exist
    for dir_path in directories.values():
        os.makedirs(dir_path, exist_ok=True)

    # Generate files in the respective directories
    for file_type, template in TEMPLATES.items():
        file_content = template.format(
            class_name=class_name,
            model_name=model_name,
            model_path=model_path,
            labels=labels,
            task=task,
            data_name=data_name
        )
        file_name = f"{model_name.lower()}.py"
        file_path = os.path.join(directories[file_type], file_name)
        with open(file_path, "w") as f:
            f.write(file_content)
        print(f"Created: {file_path}")

      # Add import line to __init__.py in the respective directory
        if file_type != "configs":
            init_file_path = os.path.join(directories[file_type], "__init__.py")
            import_line = f"from .{model_name.lower()} import {class_name}\n"
            with open(init_file_path, "a") as init_file:
                init_file.write(import_line)
            print(f"Updated: {init_file_path}")