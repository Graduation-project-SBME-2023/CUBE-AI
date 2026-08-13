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
import os
import inspect
import nibabel as nib
from typing import Optional
import numpy as np
from nnInteractive.inference.inference_session import nnInteractiveInferenceSession

from monailabel.tasks.infer.basic_infer import BasicInferTask
from monailabel.interfaces.tasks.infer_v2 import InferType
import torch
import tempfile
from storage_paths import MODELS_DIR, TEMP_DIR
from pathlib import Path
import SimpleITK as sitk
import logging



logger = logging.getLogger(__name__)


class NNINTERACTIVE(BasicInferTask):
    """
    This provides Inference Engine for pre-trained nnInteractive segmentation model.
    """

    def __init__(
        self,
        path,
        network=None,
        target_spacing=(1.5, 1.5, 1.5),
        type=InferType.SEGMENTATION,
        labels=None,
        dimension=2,
        description="A pre-trained model for volumetric  segmentation of the monai nnInteractive",
        **kwargs,
    ):
        super().__init__(
            path=path,
            network=network,
            type=type,
            labels=labels,
            dimension=dimension,
            description=description,
            **kwargs,
        )
        self.target_spacing = target_spacing
        self.BASE_PATH = Path(os.getenv("MODELS_DIR", str(MODELS_DIR)))
        self.output_dir = TEMP_DIR / "radiology" / "nninteractive"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session=None


    def is_valid(self) -> bool:
        return True

    def pre_transforms(self, data=None) -> Sequence[Callable]:
        return []

    def get_point_interactions(self, data):
        """Validate UI prompts and convert them to ``(point, is_positive)`` pairs."""
        prompts = data.get("Points") or {}
        points = prompts.get("points") or []
        labels = prompts.get("labels") or []
        if len(points) != len(labels):
            raise ValueError("Points.points and Points.labels must have the same length")

        image_shape = nib.load(data[self.input_key]).shape[:3]
        interactions = []
        for point, label in zip(points, labels):
            if isinstance(point, dict):
                point = [point.get(str(index), point.get(index)) for index in range(3)]
            if not isinstance(point, (list, tuple, np.ndarray)) or len(point) != 3:
                raise ValueError(f"Invalid nnInteractive point: {point!r}")

            coordinates = tuple(int(round(float(value))) for value in point)
            if any(value < 0 or value >= image_shape[index] for index, value in enumerate(coordinates)):
                raise ValueError(f"nnInteractive point {coordinates} is outside image bounds {image_shape}")
            interactions.append((coordinates, bool(int(label))))

        if not interactions:
            raise ValueError("At least one positive or negative point is required for nnInteractive")
        return interactions


    # Intilization function
    def initialize_session(self, device: str = "cuda"):
        # nnInteractive changed optional constructor arguments across releases.
        # Only pass options advertised by the installed package so a container
        # upgrade cannot prevent a segmentation session from being created.
        options = {
            "device": torch.device(device),
            "use_torch_compile": False,
            "verbose": True,
            "torch_n_threads": os.cpu_count(),
            "do_autozoom": True,
        }
        supported = inspect.signature(nnInteractiveInferenceSession).parameters
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in supported.values()
        )
        session = nnInteractiveInferenceSession(
            **(options if accepts_kwargs else {name: value for name, value in options.items() if name in supported})
        )
        logger.info("session initialized")
        return session

    # load the model
    def load_model(self, session, model_path):
        if not os.path.isdir(model_path):
            raise FileNotFoundError(f"nnInteractive model directory does not exist: {model_path}")
        session.initialize_from_trained_model_folder(model_path)
        logger.info("nnInteractive model loaded from %s", model_path)


    def load_volume(self,volume_path):
        input_image = sitk.ReadImage(volume_path)
        arr = sitk.GetArrayFromImage(input_image)          # (Z, Y, X)
        arr = np.transpose(arr, (2, 1, 0))              # reorder to (X, Y, Z)
        img_tensor = torch.from_numpy(arr).float().unsqueeze(0)  # (1, X, Y, Z)

        return img_tensor

    def nnInteractive_segmentation(self,volume_path,
                        output_path,
                        model_path,
                        point_interactions,
                        device: str = "cuda",
                        Interactive: bool = True,
                        new_object=False,
                        reset_session: bool = False,
                        session: Optional[nnInteractiveInferenceSession] = None) -> nnInteractiveInferenceSession:
        img = self.load_volume(volume_path)

        # Store the current image info for comparison
        current_shape = img.shape
        current_volume_path = volume_path

        # Session management - Enhanced condition checking
        should_reset_session = (
            session is None or
            reset_session or
            not hasattr(session, '_current_image_shape') or
            session._current_image_shape != current_shape or
            not hasattr(session, '_current_volume_path') or
            session._current_volume_path != current_volume_path
        )

        if should_reset_session:
            logger.info(f"Initializing new session. Reason: {'no session' if session is None else 'reset requested' if reset_session else 'different image'}")
            session = self.initialize_session(device)
            # nnInteractive derives its preprocessing pipeline from the model
            # configuration, so load the trained model before accepting an image.
            self.load_model(session, model_path)
            session.set_image(img.numpy())
            target_tensor = torch.zeros(img.shape[1:], dtype=torch.uint8)
            session.set_target_buffer(target_tensor)

            # Store session metadata for future comparisons
            session._current_image_shape = current_shape
            session._current_volume_path = current_volume_path
            logger.info(f"model path {model_path}")
        elif new_object:
            logger.info("Resetting interactions for new object on same image")
            session.reset_interactions()
            # Reset target buffer for new object
            if hasattr(session, 'target_buffer') and session.target_buffer is not None:
                if isinstance(session.target_buffer, torch.Tensor):
                    session.target_buffer.zero_()
                elif isinstance(session.target_buffer, np.ndarray):
                    session.target_buffer.fill(0)

        # Load model if not already loaded (for existing sessions)
        if not hasattr(session, 'model'):
            self.load_model(session, model_path)

        for point_coordinates, include_interaction in point_interactions:
            logger.info("Applying nnInteractive point %s (positive=%s)", point_coordinates, include_interaction)
            session.add_point_interaction(point_coordinates, include_interaction=include_interaction)

        results = session.target_buffer
        results_np = results.detach().cpu().numpy() if isinstance(results, torch.Tensor) else np.asarray(results)

        # Load the original image to get spatial information
        ref_img = nib.load(volume_path)
        affine = ref_img.affine

        # Save the transformed segmentation result as a NIfTI file
        seg_img = nib.Nifti1Image(results_np, affine)

        os.makedirs(output_path, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            dir=output_path,
            prefix="nninteractive-seg-",
            suffix=".nii.gz",
            delete=False,
        ) as stream:
            output_file = stream.name

        nib.save(seg_img, output_file)
        logger.info("Segmentation result saved as %s", output_file)

        return session, output_file


    def run_inferer(self, data, convert_to_batch=True, device="cuda"):

        logger.info("Running nnInteractive inference")
        point_interactions = self.get_point_interactions(data)

        self.session, output_path=self.nnInteractive_segmentation(
            volume_path=data[self.input_key],
            output_path=str(self.output_dir),
            model_path=os.path.join(self.BASE_PATH,"nnInteractive_v1.0"),
            point_interactions=point_interactions,
            new_object=bool(data.get('resetInteractions', False)),
            session=self.session,
            device=device
        )
        if device.startswith("cuda"):
            torch.cuda.empty_cache()

        if os.path.exists(output_path):
            try:
                outputs = torch.from_numpy(nib.load(output_path).get_fdata())
            finally:
                os.unlink(output_path)
            unique_values = torch.unique(outputs)
            logger.info(f"[INFO] Unique values in mask: {unique_values}")
            data[self.output_label_key] = outputs
        else:
            raise RuntimeError(f"Expected nnInteractive output file not found: {output_path}")

        return data


    def inverse_transforms(self, data=None):
        return []

    def post_transforms(self, data=None) -> Sequence[Callable]:
        return []
