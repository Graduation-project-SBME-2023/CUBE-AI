"""The image/mask pairs an extraction measures.

Only the accepted and modified pairs of a submitted segmentation review are
measured: every radiomics row names the SEG and segment it measured, so a
mask with no reviewed SEG identity could only produce unattributable numbers.
A pair's image is found in the image directory by the volume's uid.
"""

from __future__ import annotations

import glob
import logging
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ...common.workspace import base_on_workspace_dir

logger = logging.getLogger(__name__)

NIFTI_EXTENSIONS = (".nii.gz", ".nii")
# Where a workspace keeps its volumes, in the order they are looked for.
IMAGE_DIR_CANDIDATES = ("volumes", "images", "series", "data/volumes", "data/images", "data")


@dataclass(frozen=True)
class MaskPair:
    """One reviewed target on one volume: its image, its mask and the SEG
    segment the mask is."""

    pair_key: str
    image: str
    mask: str
    volume_uid: str
    target_key: str
    segmentation_pair_id: str
    label_series_instance_uid: str
    segment_number: int


def normalise_id(value: Any) -> str:
    """A volume id compared case-insensitively, without a NIfTI extension."""
    text = str(value).strip().casefold()
    for extension in NIFTI_EXTENSIONS:
        if text.endswith(extension):
            return text[: -len(extension)]
    return text


def volume_id(path: str, id_pattern: str | None = None) -> str:
    """The volume id an image file is named by: ``id_pattern``'s first group
    when it matches, else the file name without its extension."""
    filename = os.path.basename(path)
    if id_pattern:
        try:
            match = re.search(id_pattern, filename)
        except re.error:
            match = None
        if match and match.groups():
            return match.group(1)
    for extension in NIFTI_EXTENSIONS:
        if filename.endswith(extension):
            return filename[: -len(extension)]
    return os.path.splitext(filename)[0]


def image_paths(directory: str, pattern: str) -> list[str]:
    return sorted(glob.glob(os.path.join(directory, pattern)))


def find_image_dir(requested: str | None, pattern: str) -> str:
    """The requested directory when it exists; otherwise the first workspace
    candidate holding matching images (or the first that exists at all)."""
    requested_dir = base_on_workspace_dir(requested) if requested else None
    if requested_dir and os.path.isdir(requested_dir):
        return requested_dir
    fallback = None
    for candidate in IMAGE_DIR_CANDIDATES:
        path = base_on_workspace_dir(candidate)
        if path and os.path.isdir(path):
            if image_paths(path, pattern):
                found = path
                break
            fallback = fallback or path
    else:
        found = fallback or requested_dir or base_on_workspace_dir("volumes")
    if requested_dir and requested_dir != found:
        logger.warning("image_dir %r not found; using %r", requested_dir, found)
    return found


def reviewed_pairs(
    reviewed: Iterable[Mapping[str, Any]], images: Mapping[str, str]
) -> dict[str, MaskPair]:
    """The reviewed pairs whose image and mask are on disk, by pair key."""
    pairs: dict[str, MaskPair] = {}
    for item in reviewed:
        volume_uid = str(item.get("volume_series_instance_uid") or "")
        target_key = str(item.get("target_key") or "")
        pair_key = str(item.get("pair_key") or "")
        label_uid = str(item.get("selected_segmentation_uid") or "")
        mask_rel = str((item.get("execution_checkpoint") or {}).get("mask_path") or "")
        image = images.get(volume_uid)
        mask = base_on_workspace_dir(mask_rel) if mask_rel else None
        if not (volume_uid and target_key and pair_key and label_uid and image and mask):
            logger.warning(
                "Reviewed pair %s has no image or mask on disk, or no selected SEG; skipping",
                pair_key or volume_uid,
            )
            continue
        if not os.path.isfile(mask):
            logger.warning("Mask %s of reviewed pair %s is missing; skipping", mask, pair_key)
            continue
        pairs[pair_key] = MaskPair(
            pair_key=pair_key,
            image=image,
            mask=mask,
            volume_uid=volume_uid,
            target_key=target_key,
            segmentation_pair_id=str(item.get("pair_id") or ""),
            label_series_instance_uid=label_uid,
            segment_number=int(item.get("segment_number") or 1),
        )
    return pairs


__all__ = [
    "MaskPair",
    "find_image_dir",
    "image_paths",
    "normalise_id",
    "reviewed_pairs",
    "volume_id",
]
