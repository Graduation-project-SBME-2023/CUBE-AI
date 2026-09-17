"""Encode and decode DICOM SEG, without any transport concerns.

Segmentation results already reach Orthanc as DICOM SEG.  What was missing is the
other direction: nothing could read a SEG back out and turn it into the NIfTI
that PyRadiomics and the preview renderer expect, so the workspace copy produced
by MONAI was the only usable form of a mask.

Both directions live here as pure functions over ``pydicom`` datasets and numpy
arrays, so the geometry can be proved with a synthetic volume.

Array convention matches :mod:`imaging.geometry`: masks are ``(z, y, x)`` --
slices, rows, columns -- in the source series' own physical order.
"""

from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path
from typing import Literal, Sequence

import highdicom as hd
import numpy as np
import pydicom
import SimpleITK as sitk
from pydicom.sr.codedict import codes
from pydicom.uid import generate_uid

from imaging.geometry import (
    VolumeGeometry,
    geometry_from_dicom_series,
    geometry_from_sitk,
    sitk_image_from_array,
    sort_dicom_series,
)

logger = logging.getLogger(__name__)

# Must stay equal to SEGMENTATION_GEOMETRY_VERSION in
# agents/execution_agents/segmentation/utils.py -- a contract test asserts it.
# Duplicated rather than imported so this shared module does not depend on a
# feature package.
SEG_GEOMETRY_VERSION = "cubeai-geometry-v2"

# Sibling of the ingestion branch (...511.4.); segmentations already use ...511.3.
SEG_UID_ROOT = "1.2.826.0.1.3680043.10.511.3."

MANUFACTURER = "CUBE.AI"
AUTO_MODEL_NAME = "MONAI Label Backend"
MANUAL_MODEL_NAME = "Interactive Review"

# sync_series_to_db in application_api derives method="manual" from this word
# appearing in the series description, so editing it changes the database.
MANUAL_DESCRIPTION_MARKER = "MANUAL"

Method = Literal["auto", "manual"]


def deterministic_seg_uids(*entropy: str) -> tuple[str, str]:
    """Series and SOP UIDs that repeat for the same inputs.

    Re-running a segmentation must reconcile with the SEG already in Orthanc
    instead of creating a second copy of the same mask.
    """
    parts = [str(part) for part in entropy if str(part)]
    if not parts:
        raise ValueError("at least one entropy source is required for a stable UID")
    return (
        generate_uid(prefix=SEG_UID_ROOT, entropy_srcs=[*parts, "series"]),
        generate_uid(prefix=SEG_UID_ROOT, entropy_srcs=[*parts, "instance"]),
    )


def build_seg_dataset(
    *,
    mask_zyx: np.ndarray,
    source_images: Sequence[pydicom.Dataset],
    segment_label: str,
    series_instance_uid: str,
    sop_instance_uid: str,
    series_description: str,
    method: Method = "auto",
    model_name: str | None = None,
    derived_from_seg_uid: str | None = None,
    series_number: int = 1001,
) -> hd.seg.Segmentation:
    """Encode a binary mask against the images it was drawn on.

    ``source_images`` must be the real source series: highdicom copies the frame
    of reference and per-frame geometry from it, which is what lets a viewer put
    the mask back in the right place.
    """
    mask = np.asarray(mask_zyx)
    if mask.ndim != 3:
        raise ValueError(f"expected a 3-D (z, y, x) mask, received shape {mask.shape}")
    if not source_images:
        raise ValueError("a DICOM SEG needs its source images")

    ordered = sort_dicom_series(source_images)
    expected = (len(ordered), int(ordered[0].Rows), int(ordered[0].Columns))
    if tuple(mask.shape) != expected:
        raise ValueError(
            f"mask shape {tuple(mask.shape)} does not match the source series {expected}"
        )

    binary = (mask > 0).astype(np.uint8)
    if not binary.any():
        # An empty SEG carries no information and some writers reject a pixel
        # array with no encoded segment.
        raise ValueError("refusing to encode a DICOM SEG with no foreground voxels")

    label = str(segment_label or "Segment").strip() or "Segment"
    is_manual = method == "manual"

    if is_manual:
        # highdicom requires no algorithm identification for a manual segment,
        # and the distinction is real: a clinician drew this, a model did not.
        algorithm_type = hd.seg.SegmentAlgorithmTypeValues.MANUAL
        algorithm_identification = None
    else:
        algorithm_type = hd.seg.SegmentAlgorithmTypeValues.AUTOMATIC
        algorithm_identification = hd.AlgorithmIdentificationSequence(
            name=str(model_name or AUTO_MODEL_NAME),
            version=SEG_GEOMETRY_VERSION,
            family=codes.cid7162.ArtificialIntelligence,
        )

    segment = hd.seg.SegmentDescription(
        segment_number=1,
        segment_label=label,
        segmented_property_category=codes.cid7150.Tissue,
        segmented_property_type=codes.cid7166.ConnectiveTissue,
        algorithm_type=algorithm_type,
        algorithm_identification=algorithm_identification,
        # Derived from the series UID so re-encoding the same mask is stable.
        tracking_uid=generate_uid(
            prefix=SEG_UID_ROOT, entropy_srcs=[series_instance_uid, "tracking"]
        ),
        tracking_id=label,
    )

    segmentation = hd.seg.Segmentation(
        source_images=list(ordered),
        pixel_array=binary,
        segmentation_type=hd.seg.SegmentationTypeValues.BINARY,
        segment_descriptions=[segment],
        series_instance_uid=series_instance_uid,
        sop_instance_uid=sop_instance_uid,
        series_number=series_number,
        series_description=series_description,
        instance_number=1,
        manufacturer=MANUFACTURER,
        manufacturer_model_name=str(
            model_name or (MANUAL_MODEL_NAME if is_manual else AUTO_MODEL_NAME)
        ),
        software_versions=SEG_GEOMETRY_VERSION,
        device_serial_number=SEG_GEOMETRY_VERSION,
        # Keeping empty frames means the frame count always equals the slice
        # count, which makes reading the mask back trivial.
        omit_empty_frames=False,
    )

    if derived_from_seg_uid:
        # Records which segmentation a clinician edited, so the AI original and
        # the corrected version stay linked in the archive.
        segmentation.DerivationDescription = (
            f"Edited from segmentation series {derived_from_seg_uid}"
        )
        block = segmentation.private_block(0x0099, MANUFACTURER, create=True)
        block.add_new(0x10, "LO", str(derived_from_seg_uid)[:64])
        block.add_new(0x11, "LO", "manual-edit")

    logger.info(
        "Encoded DICOM SEG %s: label=%s method=%s frames=%s",
        series_instance_uid,
        label,
        method,
        getattr(segmentation, "NumberOfFrames", None),
    )
    return segmentation


def manual_series_description(target_label: str, source_description: str | None = None) -> str:
    """A description the Application API will classify as a manual segmentation."""
    base = (source_description or f"Segmentation - {target_label}").strip()
    if MANUAL_DESCRIPTION_MARKER in base.upper():
        return base[:64]
    return f"{base} {MANUAL_DESCRIPTION_MARKER}"[:64]


def seg_to_mask_array(
    seg: pydicom.Dataset,
    source_images: Sequence[pydicom.Dataset],
) -> np.ndarray:
    """Decode a DICOM SEG back into a ``(z, y, x)`` binary mask.

    The mask is rebuilt against the source instances in physical order, so the
    result lines up with the image volume regardless of how the SEG stored or
    omitted its frames.
    """
    if not source_images:
        raise ValueError("decoding a DICOM SEG needs its source images")

    ordered = sort_dicom_series(source_images)
    sop_uids = [str(ds.SOPInstanceUID) for ds in ordered]

    parsed = hd.seg.Segmentation.from_dataset(seg, copy=False)
    read = lambda **extra: parsed.get_pixels_by_source_instance(  # noqa: E731
        source_sop_instance_uids=sop_uids,
        segment_numbers=[1],
        # A SEG written with omit_empty_frames has no frame for blank slices;
        # those slices are empty, not missing.
        assert_missing_frames_are_empty=True,
        **extra,
    )

    try:
        pixels = read()
    except RuntimeError as exc:
        # DerivationImageSequence carries SpatialLocationsPreserved, and a writer
        # that leaves it out makes highdicom refuse to index by source instance.
        # Ours always sets it, but MONAI Label's re-export of the same mask does
        # not -- and refusing to read is worse than reading it: the frames are
        # still keyed to these very source instances, which is the only thing
        # the flag would have told us. Fall back rather than lose the mask.
        if "spatial locations" not in str(exc).lower():
            raise
        logger.warning(
            "SEG %s does not declare that spatial locations are preserved; "
            "decoding it against its referenced source instances anyway",
            getattr(seg, "SeriesInstanceUID", "<unknown>"),
        )
        pixels = read(ignore_spatial_locations=True)

    mask = np.asarray(pixels)[..., 0]
    return (mask > 0).astype(np.uint8)


def seg_to_nifti_bytes(
    seg: pydicom.Dataset,
    source_images: Sequence[pydicom.Dataset],
) -> bytes:
    """Decode a DICOM SEG straight into gzipped NIfTI bytes."""
    ordered = sort_dicom_series(source_images)
    mask = seg_to_mask_array(seg, ordered)
    geometry = geometry_from_dicom_series(ordered)
    return mask_to_nifti_bytes(mask, geometry)


def write_mask_nifti(mask_zyx: np.ndarray, geometry: VolumeGeometry, path: Path) -> Path:
    """Write a mask to a NIfTI file, preserving its physical placement."""
    image = sitk_image_from_array(np.asarray(mask_zyx).astype(np.uint8), geometry)
    path.parent.mkdir(parents=True, exist_ok=True)
    sitk.WriteImage(image, str(path), useCompression=True)
    return path


def mask_to_nifti_bytes(mask_zyx: np.ndarray, geometry: VolumeGeometry) -> bytes:
    """Serialise a mask as gzipped NIfTI.

    SimpleITK has no in-memory writer, so this goes through a temporary file.
    Prefer :func:`write_mask_nifti` when the destination is a file anyway.
    """
    with tempfile.TemporaryDirectory() as directory:
        return write_mask_nifti(
            mask_zyx, geometry, Path(directory) / "mask.nii.gz"
        ).read_bytes()


def nifti_to_mask(path: Path) -> tuple[np.ndarray, VolumeGeometry]:
    """Read a NIfTI mask file back into an array and its geometry."""
    image = sitk.ReadImage(str(path))
    return sitk.GetArrayFromImage(image).astype(np.uint8), geometry_from_sitk(image)


def seg_bytes(segmentation: hd.seg.Segmentation) -> bytes:
    """Serialise a segmentation for upload."""
    buffer = io.BytesIO()
    segmentation.save_as(buffer)
    buffer.seek(0)
    return buffer.read()
