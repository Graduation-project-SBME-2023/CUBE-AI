"""One reviewed pair's masks, prepared for the extractor.

A pair is one target on one SEG segment. A single-label mask file is that
segment; a multi-label file is measured only on the label equal to the
pair's segment number. Every prepared mask carries its content digests —
decoded content, not file bytes, so a re-converted NIfTI of the same series
digests identically — and the cached features, if this exact extraction was
already run. ``prepare_pair`` reads and writes images: run it off the event loop.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .. import digests as provenance
from ..cache_utils import get_cache_key, load_from_cache, save_to_cache
from ..validation_utils import check_mask_voxels, check_spatial_alignment
from .pairs import MaskPair

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class PreparedMask:
    label: int
    segment_number: int
    path: str  # the binary mask file the extractor reads
    segmentation_digest: str
    source_digest: str
    cache_key: str
    cached: dict[str, Any] | None = None


@dataclass
class PreparedPair:
    pair: MaskPair
    image_digest: str
    masks: list[PreparedMask] = field(default_factory=list)
    warning: str | None = None  # a spatial mismatch, or why a mask was too small


def label_plan(present_labels: list[int], segment_number: int) -> list[tuple[int, int]]:
    """``(mask label, SEG segment)`` pairs to measure."""
    if len(present_labels) == 1:
        return [(present_labels[0], segment_number)]
    if segment_number in present_labels:
        return [(segment_number, segment_number)]
    return []


def prepare_pair(
    pair: MaskPair,
    *,
    config_digest: str,
    temp_dir: str,
    cache_dir: str,
    image_digests: dict[str, str],
    requested_labels: set[int] | None = None,
) -> PreparedPair:
    """Read the pair's image and mask, write one binary mask per measured
    label into ``temp_dir``, and look each up in the extraction cache.
    ``image_digests`` caches image digests across pairs of one volume."""
    import SimpleITK as sitk

    image = sitk.ReadImage(pair.image)
    mask = sitk.ReadImage(pair.mask)
    mask_array = sitk.GetArrayFromImage(mask)
    if pair.image not in image_digests:
        image_digests[pair.image] = provenance.image_digest(image)
    prepared = PreparedPair(pair=pair, image_digest=image_digests[pair.image])

    present = sorted(int(value) for value in np.unique(mask_array) if value > 0)
    plan = label_plan(present, pair.segment_number)
    if not plan:
        logger.warning(
            "%s: mask labels %s do not identify segment %s of %s; skipping",
            pair.volume_uid, present, pair.segment_number, pair.label_series_instance_uid,
        )
    if requested_labels is not None:
        plan = [(label, segment) for label, segment in plan if label in requested_labels]

    aligned, message = check_spatial_alignment(pair.image, pair.mask)
    if not aligned and message:
        prepared.warning = message
        logger.warning("%s: %s", pair.volume_uid, message)

    for label, segment_number in plan:
        binary = (mask_array == label).astype(np.uint8)
        if not binary.any():
            continue
        valid, voxels = check_mask_voxels(binary)
        if not valid:
            reason = f"Mask_too_small (label {label}, {voxels} voxels < 10)"
            logger.warning("%s: %s; not measured", pair.volume_uid, reason)
            prepared.warning = prepared.warning or reason
            continue
        binary_image = sitk.GetImageFromArray(binary)
        binary_image.CopyInformation(mask)
        path = os.path.join(temp_dir, f"{_SAFE_NAME.sub('_', pair.pair_key)}_lbl{label}.nii.gz")
        sitk.WriteImage(binary_image, path)
        segmentation_digest = provenance.segmentation_digest(
            binary,
            mask,
            label_series_instance_uid=pair.label_series_instance_uid,
            segment_number=segment_number,
        )
        source_digest = provenance.source_digest(
            image_digest=prepared.image_digest,
            segmentation_digest=segmentation_digest,
            config_digest=config_digest,
            label_series_instance_uid=pair.label_series_instance_uid,
            segment_number=segment_number,
        )
        # Content-addressed: a changed setting or intensity misses; paths and
        # file times are not part of the key.
        cache_key = get_cache_key(prepared.image_digest, segmentation_digest, config_digest)
        prepared.masks.append(PreparedMask(
            label=label,
            segment_number=segment_number,
            path=path,
            segmentation_digest=segmentation_digest,
            source_digest=source_digest,
            cache_key=cache_key,
            cached=load_from_cache(cache_dir, cache_key),
        ))
    return prepared


def remember(cache_dir: str, prepared: PreparedMask, features: dict[str, Any]) -> None:
    """Keep the features of one extraction in the (disposable) cache."""
    save_to_cache(cache_dir, prepared.cache_key, features)


def discard(prepared: PreparedPair) -> None:
    """Remove the pair's binary mask files."""
    for item in prepared.masks:
        try:
            os.remove(item.path)
        except FileNotFoundError:
            pass


__all__ = ["PreparedMask", "PreparedPair", "discard", "label_plan", "prepare_pair", "remember"]
