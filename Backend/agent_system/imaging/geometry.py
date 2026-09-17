"""Physical-geometry helpers shared by DICOM ingestion and segmentation.

Conventions used throughout this module
---------------------------------------
* Physical space is DICOM LPS (Left, Posterior, Superior).  SimpleITK stores
  geometry in LPS too, so a ``sitk`` image needs no axis flipping here.  NIfTI
  holds RAS on disk, but ``sitk.ReadImage`` converts it on load -- which is why
  volumes are read through SimpleITK rather than nibabel.
* Voxel arrays are ordered ``(z, y, x)`` -- slices, rows, columns.  That is what
  ``sitk.GetArrayFromImage`` returns and what a DICOM series consumes when it is
  written one slice at a time.
* ``VolumeGeometry.direction`` is a 3x3 matrix whose *columns* are the unit
  vectors of increasing i (columns), j (rows) and k (slices), expressed in LPS.
  This matches ``SegmentationService._dicom_reference_geometry`` so masks and
  ingested images share one convention.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pydicom
import SimpleITK as sitk

logger = logging.getLogger(__name__)

# Direction cosines are unit vectors; anything looser than this is a real error
# rather than floating-point noise.
_UNIT_TOLERANCE = 1e-4
_MIN_DETERMINANT = 1e-6


@dataclass(frozen=True)
class VolumeGeometry:
    """Physical placement of a voxel grid in DICOM LPS space."""

    size_xyz: tuple[int, int, int]
    spacing_xyz: tuple[float, float, float]
    origin_lps: tuple[float, float, float]
    direction: np.ndarray

    @property
    def columns(self) -> int:
        return int(self.size_xyz[0])

    @property
    def rows(self) -> int:
        return int(self.size_xyz[1])

    @property
    def slices(self) -> int:
        return int(self.size_xyz[2])

    @property
    def axis_i(self) -> np.ndarray:
        """Direction of increasing column index (across a DICOM row)."""
        return np.asarray(self.direction, dtype=float)[:, 0]

    @property
    def axis_j(self) -> np.ndarray:
        """Direction of increasing row index (down a DICOM column)."""
        return np.asarray(self.direction, dtype=float)[:, 1]

    @property
    def axis_k(self) -> np.ndarray:
        """Direction of increasing slice index (the slice normal)."""
        return np.asarray(self.direction, dtype=float)[:, 2]

    @property
    def array_shape_zyx(self) -> tuple[int, int, int]:
        return (self.slices, self.rows, self.columns)


def validate_geometry(geometry: VolumeGeometry) -> VolumeGeometry:
    """Reject geometry that would silently produce a misplaced series."""
    direction = np.asarray(geometry.direction, dtype=float)
    if direction.shape != (3, 3):
        raise ValueError(f"direction must be 3x3, received {direction.shape}")

    size = np.asarray(geometry.size_xyz, dtype=float)
    spacing = np.asarray(geometry.spacing_xyz, dtype=float)
    origin = np.asarray(geometry.origin_lps, dtype=float)

    if not np.all(np.isfinite(direction)):
        raise ValueError("direction contains non-finite values")
    if not np.all(np.isfinite(spacing)) or np.any(spacing <= 0):
        raise ValueError(
            f"spacing must be finite and positive, received {geometry.spacing_xyz}"
        )
    if not np.all(np.isfinite(origin)):
        raise ValueError("origin contains non-finite values")
    if np.any(size < 1):
        raise ValueError(
            f"size must be at least 1 along every axis, received {geometry.size_xyz}"
        )

    norms = np.linalg.norm(direction, axis=0)
    if not np.allclose(norms, 1.0, atol=_UNIT_TOLERANCE):
        raise ValueError(
            f"direction columns must be unit vectors, received norms {norms.tolist()}"
        )

    determinant = float(np.linalg.det(direction))
    if abs(determinant) < _MIN_DETERMINANT:
        raise ValueError(f"direction is degenerate (determinant {determinant})")

    return geometry


def geometry_from_sitk(image: sitk.Image) -> VolumeGeometry:
    """Read geometry from a SimpleITK image (already LPS, whatever the source format)."""
    if image.GetDimension() != 3:
        raise ValueError(
            f"expected a 3-D image, received {image.GetDimension()} dimensions"
        )

    size = image.GetSize()
    return validate_geometry(
        VolumeGeometry(
            size_xyz=(int(size[0]), int(size[1]), int(size[2])),
            spacing_xyz=tuple(float(value) for value in image.GetSpacing()),
            origin_lps=tuple(float(value) for value in image.GetOrigin()),
            # SimpleITK flattens row-major; reshaping restores columns = i/j/k axes.
            direction=np.asarray(image.GetDirection(), dtype=float).reshape(3, 3),
        )
    )


def sitk_image_from_array(array_zyx: np.ndarray, geometry: VolumeGeometry) -> sitk.Image:
    """Rebuild a SimpleITK image from a ``(z, y, x)`` array and its geometry."""
    array = np.asarray(array_zyx)
    if array.ndim != 3:
        raise ValueError(f"expected a 3-D (z, y, x) array, received shape {array.shape}")
    if array.shape != geometry.array_shape_zyx:
        raise ValueError(
            f"array shape {array.shape} does not match geometry {geometry.array_shape_zyx}"
        )

    image = sitk.GetImageFromArray(np.ascontiguousarray(array))
    image.SetSpacing(tuple(float(value) for value in geometry.spacing_xyz))
    image.SetOrigin(tuple(float(value) for value in geometry.origin_lps))
    image.SetDirection(
        tuple(np.asarray(geometry.direction, dtype=float).flatten(order="C"))
    )
    return image


def physical_slice_position(dataset: pydicom.Dataset) -> float | None:
    """Project a slice origin onto its own normal, for physical ordering."""
    try:
        orientation = np.asarray(dataset.ImageOrientationPatient, dtype=float)
        position = np.asarray(dataset.ImagePositionPatient, dtype=float)
        if orientation.size != 6 or position.size != 3:
            return None
        normal = np.cross(orientation[:3], orientation[3:])
        if np.linalg.norm(normal) == 0:
            return None
        return float(np.dot(position, normal))
    except (AttributeError, TypeError, ValueError):
        return None


def geometry_from_dicom_series(datasets: Sequence[pydicom.Dataset]) -> VolumeGeometry:
    """Derive grid geometry from physically sorted DICOM slices.

    Mirrors ``SegmentationService._dicom_reference_geometry`` so that a mask and
    the image it was drawn on cannot disagree about their own geometry.
    """
    if not datasets:
        raise ValueError("cannot build geometry from an empty DICOM series")

    first = datasets[0]
    orientation = np.asarray(first.ImageOrientationPatient, dtype=float)
    position = np.asarray(first.ImagePositionPatient, dtype=float)
    if orientation.size != 6 or position.size != 3:
        raise ValueError(
            "ImageOrientationPatient and ImagePositionPatient are required to place a series"
        )

    row_cosine = orientation[:3] / np.linalg.norm(orientation[:3])
    col_cosine = orientation[3:] / np.linalg.norm(orientation[3:])
    normal = np.cross(row_cosine, col_cosine)
    normal = normal / np.linalg.norm(normal)

    pixel_spacing = np.asarray(first.PixelSpacing, dtype=float).reshape(-1)
    if pixel_spacing.size != 2 or np.any(pixel_spacing <= 0):
        raise ValueError("PixelSpacing must contain two positive values")

    positions = [physical_slice_position(ds) for ds in datasets]
    if any(value is None for value in positions):
        raise ValueError("every slice needs a resolvable physical position")

    positions_np = np.asarray(positions, dtype=float)
    if positions_np.size > 1:
        deltas = np.diff(positions_np)
        if np.any(deltas <= 0):
            raise ValueError("slices must be physically sorted with unique positions")
        slice_spacing = float(np.median(deltas))
    else:
        slice_spacing = float(
            getattr(first, "SpacingBetweenSlices", 0)
            or getattr(first, "SliceThickness", 1.0)
            or 1.0
        )
    if slice_spacing <= 0:
        raise ValueError("slice spacing must be positive")

    # DICOM PixelSpacing is [row, column]; index i runs across columns.
    return validate_geometry(
        VolumeGeometry(
            size_xyz=(int(first.Columns), int(first.Rows), len(datasets)),
            spacing_xyz=(
                float(pixel_spacing[1]),
                float(pixel_spacing[0]),
                slice_spacing,
            ),
            origin_lps=tuple(float(value) for value in position),
            direction=np.column_stack((row_cosine, col_cosine, normal)),
        )
    )


def sort_dicom_series(datasets: Sequence[pydicom.Dataset]) -> list[pydicom.Dataset]:
    """Order slices by physical position, falling back to InstanceNumber."""
    if not datasets:
        raise ValueError("cannot sort an empty DICOM series")

    expected_rows = int(datasets[0].Rows)
    expected_columns = int(datasets[0].Columns)
    for dataset in datasets:
        if (
            int(dataset.Rows) != expected_rows
            or int(dataset.Columns) != expected_columns
        ):
            raise ValueError("DICOM frames have inconsistent dimensions")

    positions = [physical_slice_position(ds) for ds in datasets]
    if any(value is None for value in positions):
        logger.warning(
            "ImagePositionPatient/ImageOrientationPatient unavailable; "
            "falling back to InstanceNumber ordering"
        )
        return sorted(
            datasets, key=lambda ds: int(getattr(ds, "InstanceNumber", 0) or 0)
        )

    reference = np.asarray(datasets[0].ImageOrientationPatient, dtype=float)
    for dataset in datasets[1:]:
        current = np.asarray(dataset.ImageOrientationPatient, dtype=float)
        if not np.allclose(current, reference, rtol=1e-5, atol=1e-5):
            raise ValueError("DICOM frames have inconsistent orientations")

    paired = sorted(zip(positions, datasets), key=lambda item: float(item[0]))
    sorted_positions = [float(item[0]) for item in paired]
    if len(sorted_positions) > 1:
        spacing = np.diff(sorted_positions)
        if np.any(spacing <= 0):
            raise ValueError("DICOM slices have duplicate physical positions")
        if not np.allclose(spacing, np.median(spacing), rtol=0.05, atol=1e-3):
            logger.warning("DICOM slice spacing is non-uniform: %s", spacing.tolist())
    return [dataset for _, dataset in paired]


def slice_origin_lps(geometry: VolumeGeometry, index: int) -> list[float]:
    """ImagePositionPatient for slice ``index`` (0-based)."""
    if not 0 <= index < geometry.slices:
        raise IndexError(f"slice index {index} outside 0..{geometry.slices - 1}")
    origin = np.asarray(geometry.origin_lps, dtype=float)
    offset = index * float(geometry.spacing_xyz[2]) * geometry.axis_k
    return [float(value) for value in origin + offset]


def image_orientation_patient(geometry: VolumeGeometry) -> list[float]:
    """ImageOrientationPatient: row direction first, then column direction."""
    return [float(value) for value in (*geometry.axis_i, *geometry.axis_j)]


def pixel_spacing_row_col(geometry: VolumeGeometry) -> list[float]:
    """PixelSpacing is [between rows, between columns] -- the reverse of x/y."""
    return [float(geometry.spacing_xyz[1]), float(geometry.spacing_xyz[0])]


def geometries_match(
    left: VolumeGeometry,
    right: VolumeGeometry,
    *,
    spacing_atol: float = 1e-4,
    origin_atol: float = 1e-3,
    direction_atol: float = 1e-5,
) -> bool:
    """True when two grids occupy the same physical space."""
    return bool(
        tuple(left.size_xyz) == tuple(right.size_xyz)
        and np.allclose(left.spacing_xyz, right.spacing_xyz, atol=spacing_atol)
        and np.allclose(left.origin_lps, right.origin_lps, atol=origin_atol)
        and np.allclose(
            np.asarray(left.direction, dtype=float),
            np.asarray(right.direction, dtype=float),
            atol=direction_atol,
        )
    )


def describe_geometry(geometry: VolumeGeometry) -> dict[str, Any]:
    """JSON-serialisable form for logs, job records and verification reports."""
    return {
        "size_xyz": [int(value) for value in geometry.size_xyz],
        "spacing_xyz": [float(value) for value in geometry.spacing_xyz],
        "origin_lps": [float(value) for value in geometry.origin_lps],
        "direction": [
            float(value)
            for value in np.asarray(geometry.direction, dtype=float).flatten(order="C")
        ],
    }
