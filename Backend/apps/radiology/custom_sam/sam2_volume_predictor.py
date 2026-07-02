import logging
logger = logging.getLogger(__name__)
from collections import OrderedDict

from sam2.sam2_video_predictor import SAM2VideoPredictor
from sam2.utils.misc import load_video_frames

import numpy as np
import torch


def custom_load_video_frames(
        video_path,
        image_size,
        offload_video_to_cpu,
        img_mean=(0.485, 0.456, 0.406),
        img_std=(0.229, 0.224, 0.225),
        async_loading_frames=False,
        compute_device=torch.device("cuda"),
):
    """
    Load the video frames from video_path or directly from an array/tensor. The frames are resized to image_size as in
    the model and are loaded to GPU if offload_video_to_cpu=False. This is used by the demo.
    """
    # NEW: Handle case where input is array or tensor
    if isinstance(video_path, (np.ndarray, torch.Tensor)):
        if isinstance(video_path, np.ndarray):
            video_tensor = torch.from_numpy(video_path).float()
        else:
            video_tensor = video_path.float()

        # Input is [H, W, T] — need to convert to [T, C, H, W]
        if video_tensor.dim() != 3:
            raise ValueError("Expected a 3D array [H, W, T] for grayscale video slices")

        H_orig, W_orig, T = video_tensor.shape
        video_tensor = video_tensor.permute(2, 0, 1)  # [T, H, W]

        #  Normalize each frame to [0, 255] range independently
        vmin = video_tensor.amin(dim=(1, 2), keepdim=True)
        vmax = video_tensor.amax(dim=(1, 2), keepdim=True)
        video_tensor = (video_tensor - vmin) / (vmax - vmin + 1e-5) * 255.0  # [T, H, W]

        # Apply normalization frame-by-frame if needed

        video_tensor = video_tensor.unsqueeze(1)  # Add channel: [T, 1, H, W]
        video_tensor = video_tensor.repeat(1, 3, 1, 1)  # [T, 3, H, W] # channels for color not grayscale

        # #Turns mean/std tuples into 3×1×1 tensors that Will be broadcasted across image frames for normalization
        img_mean_tensor = torch.tensor(img_mean, dtype=torch.float32)[:, None, None]
        img_std_tensor = torch.tensor(img_std, dtype=torch.float32)[:, None, None]

        # Resize to model input size
        video_tensor = torch.nn.functional.interpolate(
            video_tensor, size=(image_size, image_size), mode="bilinear", align_corners=False
        )

        # make the range from 0-255
        video_tensor = video_tensor / 255.0

        # normalize by mean and std
        video_tensor -= img_mean_tensor
        video_tensor /= img_std_tensor

        if not offload_video_to_cpu:
            video_tensor = video_tensor.to(compute_device)
            img_mean_tensor = img_mean_tensor.to(compute_device)
            img_std_tensor = img_std_tensor.to(compute_device)

        return video_tensor, H_orig, W_orig

    # Original code to handle video file path input
    return load_video_frames(
        video_path,
        image_size,
        offload_video_to_cpu,
        img_mean=(0.485, 0.456, 0.406),
        img_std=(0.229, 0.224, 0.225),
        async_loading_frames=False,
        compute_device=torch.device("cuda"),
    )


class SAM2VolumePredictor(SAM2VideoPredictor):
    @torch.inference_mode()
    def init_state(
            self,
            video_path,
            offload_video_to_cpu=False,
            offload_state_to_cpu=False,
            async_loading_frames=False,
    ):
        """Initialize an inference state."""

        #####Addded..................
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()

        # Clear existing inference state
        if hasattr(self, '_inference_state'):
            for key in list(self._inference_state.keys()):
                obj = self._inference_state[key]
                if isinstance(obj, torch.Tensor):
                    self._inference_state[key] = obj.cpu()
                elif isinstance(obj, dict):
                    for subkey in list(obj.keys()):
                        subobj = obj[subkey]
                        if isinstance(subobj, torch.Tensor):
                            obj[subkey] = subobj.cpu()
            del self._inference_state
            gc.collect()
            torch.cuda.empty_cache()

        ###Addded..................

        compute_device = self.device  # device of the model
        # @IMPPPPPPPP
        images, video_height, video_width = custom_load_video_frames(
            video_path=video_path,
            image_size=self.image_size,
            offload_video_to_cpu=offload_video_to_cpu,
            async_loading_frames=async_loading_frames,
            compute_device=compute_device,
        )
        # Save to .npz or perform debug logging if needed

        inference_state = {}
        inference_state["images"] = images
        inference_state["num_frames"] = len(images)
        # whether to offload the video frames to CPU memory
        # turning on this option saves the GPU memory with only a very small overhead
        inference_state["offload_video_to_cpu"] = offload_video_to_cpu
        # whether to offload the inference state to CPU memory
        # turning on this option saves the GPU memory at the cost of a lower tracking fps
        # (e.g. in a test case of 768x768 model, fps dropped from 27 to 24 when tracking one object
        # and from 24 to 21 when tracking two objects)
        inference_state["offload_state_to_cpu"] = offload_state_to_cpu
        # the original video height and width, used for resizing final output scores
        inference_state["video_height"] = video_height
        inference_state["video_width"] = video_width
        inference_state["device"] = compute_device
        if offload_state_to_cpu:
            inference_state["storage_device"] = torch.device("cpu")
        else:
            inference_state["storage_device"] = compute_device
        # inputs on each frame
        inference_state["point_inputs_per_obj"] = {}
        inference_state["mask_inputs_per_obj"] = {}
        # visual features on a small number of recently visited frames for quick interactions
        inference_state["cached_features"] = {}
        # values that don't change across frames (so we only need to hold one copy of them)
        inference_state["constants"] = {}
        # mapping between client-side object id and model-side object index
        inference_state["obj_id_to_idx"] = OrderedDict()
        inference_state["obj_idx_to_id"] = OrderedDict()
        inference_state["obj_ids"] = []
        # A storage to hold the model's tracking results and states on each frame
        inference_state["output_dict"] = {
            "cond_frame_outputs": {},  # dict containing {frame_idx: <out>}
            "non_cond_frame_outputs": {},  # dict containing {frame_idx: <out>}
        }
        # Slice (view) of each object tracking results, sharing the same memory with "output_dict"
        inference_state["output_dict_per_obj"] = {}
        # A temporary storage to hold new outputs when user interact with a frame
        # to add clicks or mask (it's merged into "output_dict" before propagation starts)
        inference_state["temp_output_dict_per_obj"] = {}
        # Frames that already holds consolidated outputs from click or mask inputs
        # (we directly use their consolidated outputs during tracking)
        inference_state["consolidated_frame_inds"] = {
            "cond_frame_outputs": set(),  # set containing frame indices
            "non_cond_frame_outputs": set(),  # set containing frame indices
        }
        # metadata for each tracking frame (e.g. which direction it's tracked)
        inference_state["tracking_has_started"] = False
        inference_state["frames_already_tracked"] = {}
        # Warm up the visual backbone and cache the image feature on frame 0
        self._get_image_feature(inference_state, frame_idx=0, batch_size=1)
        return inference_state
