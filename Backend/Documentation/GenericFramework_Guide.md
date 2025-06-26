# Generic Framework Documentation

### Follow the following steps before you run the backend for the first time

---

## Add this include in the main.py of the radiology app

```PYTHON
from lib.configs.generic_segmentation import GenericSegmentation as Gconf
```

## Add this line of code in the main.py of the radiology app at 102

```PYTHON
                if k == "generic_segmentation":
                    continue # To skip instantiating an object from the class generic at
```

## Add these functions in the main.py of the radiology app at 122

```PYTHON
def init_workflows(self):
     # initialize all the workflows when starting the application
        jsonPath = 'workflows_data.json'
        if os.path.exists(jsonPath):
            with open(jsonPath, "r") as file:
                data = json.load(file)
            for model_name, class_data in data.items():
                self.create_task(model_name,class_data.get('labels'),class_data.get('fold'),class_data.get('plan_type'))
                logger.info(f"+++ Adding Workflow: {model_name}")

        else:
            logger.info("No worklfows exist")




    def update_jsonfile(self, model_name, labels, fold, plan_type):
        #Function to update the json file that contains the workflows, because we will use it when the application is rebooted
        jsonPath = 'workflows_data.json'
        new_data={
            "model_name":model_name,
            "labels":labels,
            "fold":fold,
            "plan_type":plan_type

        }
        if not os.path.exists(jsonPath):
            self.create_jsonfile()

        with open(jsonPath, "r") as file:
            data = json.load(file)

        data[model_name]= new_data

        with open(jsonPath, "w") as file:
            json.dump(data, file, indent=4)

        logger.info(f"+++++ Added {model_name} workflow to the json file")

    def create_task(self, model_name, labels,fold, plan_type):
        # Update JSON file for persistence
        self.update_jsonfile(model_name, labels, fold, plan_type)

        # Initialize the TaskConfig (GenericSegmentation)
        model_config = Gconf(
            labels=labels,
            model_name=model_name,
            fold=fold,
            plan_type=plan_type,
        )

        # Initialize the model configuration with necessary parameters
        model_config.init(
            name=model_name,
            model_dir=self.model_dir,
            conf=self.conf,
            planner=self.planner
        )

        # Add the model configuration to the app's models
        self.models[model_name] = model_config

        # Generate and register the TrainTask if available
        trainer_task = model_config.trainer()
        if trainer_task is not None:
            self._trainers[model_name] = trainer_task

        logger.info(f"Successfully added model '{model_name}'  in trainer tasks.")

    def initialize_inference_task(self, model_name):

        # Generate and register the InferTask
        infer_task = self.models[model_name].infer()

        if isinstance(infer_task, dict):
            for key, task in infer_task.items():
                self._infers[key] = task
        else:
            self._infers[model_name] = infer_task


        return f"Successfully initialized inference task for model {model_name}"




    def create_jsonfile(self):
        #function to create a json file for the workflows, if it doesn't exist
        empty_data = {}
        jsonPath = 'workflows_data.json'

        with open(jsonPath, 'w') as file:
            json.dump(empty_data, file)
        logger.info("+++++++++ workflow's json file is created")

```

## Create generic_segmentation.py file in radiology\lib\configs

```PYTHON
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
```

## Create generic_segmentation.py file in radiology\lib\infers

```PYTHON
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

from batchgenerators.utilities.file_and_folder_operations import join
from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor
from pathlib import Path


logger = logging.getLogger(__name__)


class GenericSegmentation(BasicInferTask):
    def __init__(
        self,
        path,
        root_nnunet_dir,
        type=InferType.SEGMENTATION,
        labels=None,
        network=None,
        model_name=None,
        plan_type="2d",
        fold=0,
        dimension=2,
        description="A pre-trained NnUnet model for volumetric segmentation ",
        **kwargs,
    ):
        super().__init__(
            path=path,
            type=type,
            labels=labels,
            network=network,
            dimension=dimension,
            description=description,
            load_strict=True,
            **kwargs,
        )

        self.model_name=model_name
        self.plan_type = plan_type
        self.fold = fold
        self.root_nnunet_dir = root_nnunet_dir




        self.temp_path = "/temp"

        os.environ["nnUNet_raw"] = os.path.join(self.root_nnunet_dir, "nnUNet_raw")
        os.environ["nnUNet_preprocessed"] = os.path.join(self.root_nnunet_dir, "nnUNet_Preprocessed")
        os.environ["nnUNet_results"] = os.path.join(self.root_nnunet_dir, "nnUNet_Results")

        logging.info(f"nnUNet_results: {os.environ['nnUNet_results']}")


        self.task_name = self.get_data_name_from_model_name(self.model_name)


    def get_data_name_from_model_name(self, model_name):
        """
        Search for the full dataset name in the nnUNet_raw directory using only the model name.
        Example: If model_name = "blastoma", and dataset folder is "Dataset200_blastoma",
        this function returns "Dataset200_blastoma".
        """
        nnunet_raw_dir = os.environ.get("nnUNet_raw")
        if not nnunet_raw_dir or not os.path.exists(nnunet_raw_dir):
            raise FileNotFoundError(f"nnUNet_raw directory not found: {nnunet_raw_dir}")

        # Search for a directory that ends with the given model name
        for dir_name in os.listdir(nnunet_raw_dir):
            if dir_name.endswith(f"_{model_name}"):  # Match DatasetXXX_{model_name}
                return dir_name  # Return full dataset name

        raise ValueError(f"No dataset found for model name: {model_name} in {nnunet_raw_dir}")


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
        predictor.initialize_from_trained_model_folder(
            join(
                os.environ["nnUNet_results"],
                f"{self.task_name}\\nnUNetTrainer__nnUNetPlans__{self.plan_type}",
            ),
            use_folds=(self.fold,),
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

        file_ending = ".nii"
        basename = (
            os.path.basename(data[self.input_key])[: -(len(file_ending) + 5)]
            + file_ending
        )
        output_path = join(self.temp_path, basename)

        if os.path.exists(output_path):
        # if output_path:
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

```

## Create generic_segmentation.py file in radiology\lib\trainers

```PYTHON
import os
import subprocess
import logging
from monailabel.tasks.train.basic_train import BasicTrainTask, Context
from nnunetv2.training.nnUNetTrainer.nnUNetTrainer import nnUNetTrainer
import shutil
from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor

import requests
from pathlib import Path




logger = logging.getLogger(__name__)


class GenericSegmentation(BasicTrainTask):
    def __init__(  # change
        self,
        model_dir,
        model_name,
        labels,
        root_nnunet_dir,
        fold=0,
        plan_type="2d",
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
        self.labels = labels
        self.model_name = model_name
        self.fold = fold
        self.trainer_class = trainer_class
        self.model_dir=model_dir
        self.plan_type = plan_type
        self.root_nnunet_dir = root_nnunet_dir







        os.environ["nnUNet_raw"] = os.path.join(self.root_nnunet_dir, "nnUNet_raw")
        os.environ["nnUNet_preprocessed"] = os.path.join(self.root_nnunet_dir, "nnUNet_Preprocessed")
        os.environ["nnUNet_results"] = os.path.join(self.root_nnunet_dir, "nnUNet_Results")

        logger.info(f"nnUNet_raw: {os.environ['nnUNet_raw']}")
        logger.info(f"nnUNet_preprocessed: {os.environ['nnUNet_preprocessed']}")
        logger.info(f"nnUNet_results: {os.environ['nnUNet_results']}")



        # Extract the dataset name from the model name
        self.task_name = self.get_data_name_from_model_name(self.model_name)


        # Extract the numeric part from the task name
        self.task_id = ''.join(filter(str.isdigit, self.task_name))

        super().__init__(model_dir, description, **kwargs)

    # get data name from nnUnet directory by extracting the numeric part from the task name
    def get_data_name_from_model_name(self, model_name):
        """
        Search for the full dataset name in the nnUNet_raw directory using only the model name.
        Example: If model_name = "blastoma", and dataset folder is "Dataset200_blastoma",
        this function returns "Dataset200_blastoma".
        """
        nnunet_raw_dir = os.environ.get("nnUNet_raw")
        if not nnunet_raw_dir or not os.path.exists(nnunet_raw_dir):
            raise FileNotFoundError(f"nnUNet_raw directory not found: {nnunet_raw_dir}")

        # Search for a directory that ends with the given model name
        for dir_name in os.listdir(nnunet_raw_dir):
            if dir_name.endswith(f"_{model_name}"):  # Match DatasetXXX_{model_name}
                return dir_name  # Return full dataset name

        raise ValueError(f"No dataset found for model name: {model_name} in {nnunet_raw_dir}")

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

        logger.info(f"Preprocessed data saved to: {preprocessed_data_path}")

        return preprocessed_data_path

    def train(self, rank: int, world_size: int, request, datalist):
        # Train the model using the nnUNet CLI.
        logger.info("Starting nnUNet Training")

        # Ensure preprocessing is complete
        self.preprocess_dataset()

        # Run the nnUNet training command
        command = [
            "nnUNetv2_train",
             self.task_id,
             str(self.plan_type),
             str(self.fold),
            "-tr", self.trainer_class,
        ]
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Training failed with error: {e}")
            raise RuntimeError(f"Training failed for dataset {self.task_name}, fold {self.fold}, error: {e}")

        check_point_path = os.path.join(
        os.environ["nnUNet_results"],
        self.task_name,
        f"{self.trainer_class}__nnUNetPlans__{self.plan_type}",
        f"fold_{self.fold}",
        "checkpoint_best.pth"
    )



        logger.info("saving checkpoint to model_dir")

        BASE_DIR = Path(__file__).resolve().parents[3]
        destination_dir = os.path.join(BASE_DIR, "Checkpoints")
        os.makedirs(destination_dir, exist_ok=True)

        # move checkpoint_best.pth to model_dir and give it name model.pth
        # TODO: check if the this is the correct place to move checkpoint to it

        shutil.copy(check_point_path, os.path.join(destination_dir, f"pretrained_{self.model_name}.pt"))
        shutil.copy(check_point_path, os.path.join(self.model_dir, f"pretrained_{self.model_name}.pt"))

        logger.info("nnUNet Training Completed")

        self.initialize_inferer()

    def initialize_inferer(self):

        logger.info("Initializing Inference Task")


        api_url = "http://localhost:8000/initialize-inference-task/"
        payload = {
            "model_name": self.model_name,
        }

        response = requests.post(api_url, params=payload)

        if response.status_code == 200:
            logger.info(f"Inference task initialized: {response.json()}")
        else:
            logger.error(f"Failed to initialize inference task: {response.text}")

        logger.info("Training completed. Inference API triggered.")



    def val_inferer(self, context: Context):
        # Inference logic for nnUNet.

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
            os.path.join(os.environ["nnUNet_results"], self.task_name, self.trainer_class),
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

```

## create initialize_inference_task.py file in endpoints in MONAILabel

```PYTHON
from fastapi import APIRouter, Depends , Query
from monailabel.interfaces.utils.app import app_instance
from monailabel.interfaces.app import MONAILabelApp


router = APIRouter(
    prefix="/initialize-inference-task",
    tags=["Initialize Inference Task"],
    responses={404: {"description": "Not found"}},
)


@router.post("/", summary="intialize inference task")
async def initialize_inference_task(
    model_name: str = Query(..., description="Model name"),

):
    instance: MONAILabelApp = app_instance()

    message = instance.initialize_inference_task(model_name)
    return {"message": message}
```

## create create_model.py this file in endpoints in MONAILabel

```PYTHON
from fastapi import APIRouter, Depends , Query
from monailabel.interfaces.utils.app import app_instance
from monailabel.interfaces.app import MONAILabelApp


router = APIRouter(
    prefix="/create-model",
    tags=["Create Model"],
    responses={404: {"description": "Not found"}},
)


@router.post("/", summary="create model")
async def create_model(
    model_name: str = Query(..., description="Model name"),
    labels: str = Query(..., description="Labels in JSON format"),
    fold: int = Query(..., description="fold number"),
    plan_type: str = Query(..., description="plan type"),

):
    instance: MONAILabelApp = app_instance()

    message = instance.create_task(model_name, labels, fold, plan_type)
    return {"message": message}
```

## Add this lines in Lib\site-packages\monailabel\app.py

```PYTHON
app.include_router(initialize_inference_task.router, prefix=settings.MONAI_LABEL_API_STR)
app.include_router(create_model.router, prefix=settings.MONAI_LABEL_API_STR)

```

## ADD this line in Lib\site-packages\monailabel\interfaces\app.py at 106

```PYTHON
self.init_workflows()

```

## **License**

This project is licensed under the MIT License. Feel free to modify and distribute it as needed
