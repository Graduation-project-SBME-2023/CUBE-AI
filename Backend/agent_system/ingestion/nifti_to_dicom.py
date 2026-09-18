"""Convert a volume file into a DICOM series.

This module is deliberately pure: it reads a file from disk and returns
``pydicom`` datasets.  It never touches Orthanc, the database, FastAPI or a
session workspace, so the geometry -- the part that is easy to get subtly and
invisibly wrong -- can be unit tested with a small synthetic array.

Geometry is read through SimpleITK, which already reports LPS regardless of the
source format.  Doing the RAS-to-LPS flip by hand is the single most common way
to produce a left-right mirrored series that nobody notices.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

import numpy as np
import pydicom
import SimpleITK as sitk
from pydicom.dataset import FileDataset, FileMetaDataset

from imaging.geometry import (
    VolumeGeometry,
    geometry_from_sitk,
    image_orientation_patient,
    pixel_spacing_row_col,
    slice_origin_lps,
)
from ingestion.provenance import (
    EXPLICIT_VR_LITTLE_ENDIAN,
    SOURCE_LABELS,
    apply_provenance,
    ingested_uid,
    normalise_extension,
    sop_class_for_modality,
    synthetic_identity,
)

logger = logging.getLogger(__name__)

# Ingested series are numbered well away from the range a scanner produces, so
# they are easy to spot in a study list.
INGESTED_SERIES_NUMBER = 9001

# int16 storage range. The extremes are reserved so rounding cannot overflow.
_INT16_MIN = -32767
_INT16_MAX = 32767

_CHECKSUM_CHUNK_BYTES = 1024 * 1024

# pydicom 2.x needs the encoding flags set on the dataset; 3.x derives them from
# the transfer syntax and warns on every assignment.
_PYDICOM_MAJOR = int(str(pydicom.__version__).split(".", 1)[0])

# Hounsfield units are only meaningful for CT.  Claiming them anywhere else
# would assert a physical scale the file never carried.
_HOUNSFIELD_MODALITIES = frozenset({"CT"})


@dataclass(frozen=True)
class SeriesIdentity:
    """Patient and study identity for the series being created.

    Resolved by the caller: either synthesised from the file checksum, or copied
    from an existing Orthanc study so the new series lands beside real scans.
    """

    patient_id: str
    patient_name: str
    patient_birth_date: str
    patient_sex: str
    study_instance_uid: str
    study_date: str = ""
    study_time: str = ""
    accession_number: str = ""
    study_description: str = "Volume Import"
    study_id: str = ""


@dataclass
class ConversionResult:
    datasets: list[FileDataset]
    geometry: VolumeGeometry
    series_instance_uid: str
    frame_of_reference_uid: str
    checksum: str
    rescale_slope: float
    rescale_intercept: float
    rescale_type: str | None
    warnings: list[str] = field(default_factory=list)


def file_checksum(path: str | Path) -> str:
    """SHA-256 of a file, streamed so a large volume never lands in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHECKSUM_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def read_volume(path: str | Path) -> sitk.Image:
    """Load a volume file as a 3-D scalar SimpleITK image (LPS)."""
    image = sitk.ReadImage(str(path))
    if image.GetDimension() != 3:
        raise ValueError(
            f"Only 3-D volumes can be ingested; this file has "
            f"{image.GetDimension()} dimensions"
        )
    if image.GetNumberOfComponentsPerPixel() != 1:
        raise ValueError(
            "Only single-channel volumes can be ingested; this file has "
            f"{image.GetNumberOfComponentsPerPixel()} components per voxel"
        )
    return image


def synthetic_series_identity(
    checksum: str,
    *,
    study_description: str = "Volume Import",
    created_at: datetime | None = None,
) -> SeriesIdentity:
    """Build a pseudonymous identity plus a fresh study for a standalone file."""
    timestamp = created_at or datetime.now()
    identity = synthetic_identity(checksum)
    return SeriesIdentity(
        patient_id=identity["PatientID"],
        patient_name=identity["PatientName"],
        patient_birth_date=identity["PatientBirthDate"],
        patient_sex=identity["PatientSex"],
        study_instance_uid=ingested_uid(checksum, "study"),
        study_date=timestamp.strftime("%Y%m%d"),
        study_time=timestamp.strftime("%H%M%S"),
        study_description=study_description,
    )


def rescale_to_int16(
    array: np.ndarray,
) -> tuple[np.ndarray, float, float, str | None, list[str]]:
    """Map voxel values onto int16 with a DICOM rescale function.

    Integral data that already fits the stored range is passed through with an
    identity rescale, and Hounsfield units are *proposed* -- the caller confirms
    them only for CT.  Anything else is mapped linearly, which loses precision,
    so no ``RescaleType`` is claimed at all.
    """
    warnings: list[str] = []
    finite = np.isfinite(array)
    if not finite.all():
        warnings.append(
            "Volume contained non-finite voxels; they were replaced with the minimum value"
        )
        array = np.where(finite, array, np.nanmin(array[finite]) if finite.any() else 0.0)

    low = float(np.min(array))
    high = float(np.max(array))

    is_integral = np.issubdtype(array.dtype, np.integer) or bool(
        np.all(array == np.round(array))
    )
    if is_integral and _INT16_MIN <= low and high <= _INT16_MAX:
        return array.astype(np.int16), 1.0, 0.0, "HU", warnings

    if high == low:
        warnings.append("Volume is uniform; every voxel maps to the same stored value")
        return (
            np.zeros(array.shape, dtype=np.int16),
            1.0,
            low,
            None,
            warnings,
        )

    slope = (high - low) / float(_INT16_MAX - _INT16_MIN)
    intercept = low - slope * _INT16_MIN
    stored = np.round((array - intercept) / slope)
    stored = np.clip(stored, _INT16_MIN, _INT16_MAX).astype(np.int16)
    warnings.append(
        "Voxel values were rescaled linearly into int16; the stored data is an "
        "approximation and its units are unknown, so RescaleType is not set"
    )
    return stored, float(slope), float(intercept), None, warnings


def _window_from_percentiles(array: np.ndarray) -> tuple[float, float]:
    """A viewable default window, so the series is not a black screen in OHIF."""
    low, high = (float(value) for value in np.percentile(array, [1.0, 99.0]))
    width = max(high - low, 1.0)
    return float((low + high) / 2.0), width


def _new_dataset(sop_class_uid: str, sop_instance_uid: str) -> FileDataset:
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = sop_class_uid
    file_meta.MediaStorageSOPInstanceUID = sop_instance_uid
    file_meta.TransferSyntaxUID = EXPLICIT_VR_LITTLE_ENDIAN

    dataset = FileDataset(
        filename_or_obj="",
        dataset={},
        file_meta=file_meta,
        preamble=b"\x00" * 128,
    )
    if _PYDICOM_MAJOR < 3:
        dataset.is_little_endian = True
        dataset.is_implicit_VR = False
    return dataset


def convert_volume_to_dicom(
    path: str | Path,
    *,
    modality: str,
    identity: SeriesIdentity,
    checksum: str | None = None,
    series_description: str | None = None,
    body_part: str | None = None,
    source_filename: str | None = None,
    created_at: datetime | None = None,
) -> ConversionResult:
    """Convert one volume file into a list of DICOM slice datasets.

    Parameters
    ----------
    modality:
        Declared by the uploader, never guessed.  Claiming ``CT`` for data that
        is not in Hounsfield units would be a silent scientific error, and the
        modality is what cohort queries filter on.
    identity:
        Where the series belongs.  Pass ``synthetic_series_identity`` for a
        standalone file, or an identity copied from an existing study to place
        the series next to that patient's real scans.
    """
    source_path = Path(path)
    resolved_name = source_filename or source_path.name
    extension = normalise_extension(resolved_name)
    sop_class_uid = sop_class_for_modality(modality)
    resolved_checksum = checksum or file_checksum(source_path)
    timestamp = created_at or datetime.now()

    image = read_volume(source_path)
    geometry = geometry_from_sitk(image)
    array_zyx = sitk.GetArrayFromImage(image)

    stored, slope, intercept, rescale_type, warnings = rescale_to_int16(array_zyx)
    modality_key = str(modality).upper()
    if rescale_type == "HU" and modality_key not in _HOUNSFIELD_MODALITIES:
        # Integral values in the CT range do not make an MR or OT volume
        # Hounsfield; the scale stays unlabelled rather than mislabelled.
        rescale_type = None
        warnings.append(
            f"Voxel values were stored unchanged, but modality {modality_key} is not "
            "CT so no RescaleType is declared"
        )
    window_center, window_width = _window_from_percentiles(array_zyx)

    # The series identity includes everything the uploader declared, not just
    # the file's checksum. Orthanc deduplicates by SOPInstanceUID and silently
    # keeps whatever it stored first, discarding a later upload's content -- so
    # re-declaring the same bytes must mint a new series instead of colliding
    # with (and losing to) the earlier one. Body part is in here for the same
    # reason as modality: correcting it on a re-upload has to produce a series
    # that actually carries the correction. The patient/study identity stays
    # checksum-only: the same file is still the same subject however it was
    # described.
    declared = (modality_key, body_part or "")
    series_instance_uid = ingested_uid(resolved_checksum, *declared, "series")
    # A fresh frame of reference: the ingested grid is not proven to share
    # physical space with anything already in the study.
    frame_of_reference_uid = ingested_uid(
        resolved_checksum, *declared, "frame-of-reference"
    )
    orientation = image_orientation_patient(geometry)
    spacing_row_col = pixel_spacing_row_col(geometry)
    voxel_to_lps_affine = _affine_from_geometry(geometry)

    # SeriesDescription is LO, capped at 64 characters.
    description = (
        series_description
        or f"[{SOURCE_LABELS.get(extension, 'IMPORT')}] {resolved_name}"
    )[:64]

    datasets: list[FileDataset] = []
    for index in range(geometry.slices):
        sop_instance_uid = ingested_uid(
            resolved_checksum, modality_key, "instance", str(index)
        )
        dataset = _new_dataset(sop_class_uid, sop_instance_uid)

        dataset.SOPClassUID = sop_class_uid
        dataset.SOPInstanceUID = sop_instance_uid
        dataset.SeriesInstanceUID = series_instance_uid
        dataset.StudyInstanceUID = identity.study_instance_uid
        dataset.FrameOfReferenceUID = frame_of_reference_uid

        dataset.PatientID = identity.patient_id
        dataset.PatientName = identity.patient_name
        dataset.PatientBirthDate = identity.patient_birth_date
        dataset.PatientSex = identity.patient_sex

        dataset.StudyDate = identity.study_date
        dataset.StudyTime = identity.study_time
        dataset.StudyID = identity.study_id
        dataset.AccessionNumber = identity.accession_number
        dataset.StudyDescription = identity.study_description
        dataset.ReferringPhysicianName = ""

        dataset.Modality = str(modality).upper()
        # Left off entirely when nothing was declared: an absent tag reads as
        # "not stated", while an empty one reads as "stated, and blank".
        if body_part:
            dataset.BodyPartExamined = body_part
        dataset.SeriesNumber = INGESTED_SERIES_NUMBER
        dataset.SeriesDescription = description
        dataset.InstanceNumber = index + 1
        dataset.ContentDate = timestamp.strftime("%Y%m%d")
        dataset.ContentTime = timestamp.strftime("%H%M%S")

        dataset.ImageOrientationPatient = orientation
        dataset.ImagePositionPatient = slice_origin_lps(geometry, index)
        dataset.PixelSpacing = spacing_row_col
        dataset.SliceThickness = float(geometry.spacing_xyz[2])
        dataset.SpacingBetweenSlices = float(geometry.spacing_xyz[2])
        dataset.SliceLocation = float(
            np.dot(np.asarray(dataset.ImagePositionPatient, dtype=float), geometry.axis_k)
        )

        dataset.SamplesPerPixel = 1
        dataset.PhotometricInterpretation = "MONOCHROME2"
        dataset.Rows = geometry.rows
        dataset.Columns = geometry.columns
        dataset.BitsAllocated = 16
        dataset.BitsStored = 16
        dataset.HighBit = 15
        dataset.PixelRepresentation = 1
        dataset.RescaleSlope = slope
        dataset.RescaleIntercept = intercept
        if rescale_type:
            dataset.RescaleType = rescale_type
        dataset.WindowCenter = window_center
        dataset.WindowWidth = window_width
        dataset.PixelData = np.ascontiguousarray(stored[index]).tobytes()

        apply_provenance(
            dataset,
            checksum=resolved_checksum,
            source_filename=resolved_name,
            source_extension=extension,
            declared_modality=modality,
            voxel_to_lps_affine=voxel_to_lps_affine,
            converted_at=timestamp,
        )
        datasets.append(dataset)

    logger.info(
        "Converted %s to %s DICOM slices: modality=%s series=%s slope=%s intercept=%s",
        resolved_name,
        len(datasets),
        str(modality).upper(),
        series_instance_uid,
        slope,
        intercept,
    )

    return ConversionResult(
        datasets=datasets,
        geometry=geometry,
        series_instance_uid=series_instance_uid,
        frame_of_reference_uid=frame_of_reference_uid,
        checksum=resolved_checksum,
        rescale_slope=slope,
        rescale_intercept=intercept,
        rescale_type=rescale_type,
        warnings=warnings,
    )


def _affine_from_geometry(geometry: VolumeGeometry) -> list[list[float]]:
    """The 4x4 voxel-to-LPS affine, kept for auditing a conversion later."""
    affine = np.eye(4, dtype=float)
    affine[:3, :3] = np.asarray(geometry.direction, dtype=float) @ np.diag(
        np.asarray(geometry.spacing_xyz, dtype=float)
    )
    affine[:3, 3] = np.asarray(geometry.origin_lps, dtype=float)
    return affine.tolist()


def datasets_to_array(datasets: Sequence[pydicom.Dataset]) -> np.ndarray:
    """Stack sorted slices back into a ``(z, y, x)`` array of real values."""
    if not datasets:
        raise ValueError("cannot stack an empty series")
    slices = []
    for dataset in datasets:
        stored = dataset.pixel_array.astype(np.float64)
        slope = float(getattr(dataset, "RescaleSlope", 1.0) or 1.0)
        intercept = float(getattr(dataset, "RescaleIntercept", 0.0) or 0.0)
        slices.append(stored * slope + intercept)
    return np.stack(slices, axis=0)
