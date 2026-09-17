"""Geometry is the part of ingestion that fails silently, so it is tested hard.

A mirrored or shifted series still renders as a plausible-looking scan; the only
way the error surfaces is as wrong radiomics numbers months later.  These tests
use oblique and anisotropic grids on purpose, because axis-aligned isotropic
volumes hide exactly the mistakes that matter.
"""

from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk
from pydicom.dataset import Dataset

from imaging.geometry import (
    VolumeGeometry,
    geometries_match,
    geometry_from_dicom_series,
    geometry_from_sitk,
    image_orientation_patient,
    physical_slice_position,
    pixel_spacing_row_col,
    sitk_image_from_array,
    slice_origin_lps,
    sort_dicom_series,
    validate_geometry,
)

# Deliberately anisotropic: sx != sy catches a transposed PixelSpacing, which is
# invisible on the square in-plane grids most test data happens to use.
SPACING_XYZ = (0.7, 0.9, 2.5)
ORIGIN_LPS = (-120.5, -80.25, 15.0)


def _oblique_direction() -> np.ndarray:
    """A rotated but orthonormal basis, so axis-aligned shortcuts cannot pass."""
    angle = np.deg2rad(23.0)
    cos, sin = np.cos(angle), np.sin(angle)
    axis_i = np.array([cos, sin, 0.0])
    axis_j = np.array([-sin * cos, cos * cos, sin])
    axis_j = axis_j / np.linalg.norm(axis_j)
    axis_k = np.cross(axis_i, axis_j)
    axis_k = axis_k / np.linalg.norm(axis_k)
    # Re-derive j so the basis is exactly orthonormal after normalisation.
    axis_j = np.cross(axis_k, axis_i)
    return np.column_stack((axis_i, axis_j, axis_k))


def _geometry(size_xyz=(6, 5, 4)) -> VolumeGeometry:
    return validate_geometry(
        VolumeGeometry(
            size_xyz=size_xyz,
            spacing_xyz=SPACING_XYZ,
            origin_lps=ORIGIN_LPS,
            direction=_oblique_direction(),
        )
    )


def _dicom_series_from(geometry: VolumeGeometry) -> list[Dataset]:
    orientation = image_orientation_patient(geometry)
    spacing = pixel_spacing_row_col(geometry)
    datasets = []
    for index in range(geometry.slices):
        dataset = Dataset()
        dataset.Rows = geometry.rows
        dataset.Columns = geometry.columns
        dataset.PixelSpacing = spacing
        dataset.ImageOrientationPatient = orientation
        dataset.ImagePositionPatient = slice_origin_lps(geometry, index)
        dataset.SliceThickness = geometry.spacing_xyz[2]
        dataset.InstanceNumber = index + 1
        datasets.append(dataset)
    return datasets


@pytest.mark.unit
def test_sitk_round_trip_preserves_physical_placement():
    geometry = _geometry()
    array = np.arange(np.prod(geometry.array_shape_zyx), dtype=np.int16).reshape(
        geometry.array_shape_zyx
    )

    image = sitk_image_from_array(array, geometry)
    restored = geometry_from_sitk(image)

    assert geometries_match(geometry, restored)
    assert np.array_equal(sitk.GetArrayFromImage(image), array)


@pytest.mark.unit
def test_geometry_survives_a_dicom_series_round_trip():
    geometry = _geometry()

    restored = geometry_from_dicom_series(_dicom_series_from(geometry))

    assert geometries_match(geometry, restored)


@pytest.mark.unit
def test_pixel_spacing_is_row_then_column_not_x_then_y():
    # PixelSpacing is [between rows, between columns]; SimpleITK spacing is
    # (x, y, z). Swapping them is silent whenever sx == sy, so assert the swap.
    geometry = _geometry()

    assert pixel_spacing_row_col(geometry) == [SPACING_XYZ[1], SPACING_XYZ[0]]


@pytest.mark.unit
def test_image_orientation_is_column_axis_then_row_axis():
    geometry = _geometry()
    direction = np.asarray(geometry.direction, dtype=float)

    orientation = np.asarray(image_orientation_patient(geometry), dtype=float)

    # Taking matrix rows instead of columns works for axis-aligned volumes and
    # breaks for oblique ones; this asserts the columns explicitly.
    assert np.allclose(orientation[:3], direction[:, 0])
    assert np.allclose(orientation[3:], direction[:, 1])


@pytest.mark.unit
def test_slice_origins_advance_along_the_slice_normal():
    geometry = _geometry()

    first = np.asarray(slice_origin_lps(geometry, 0), dtype=float)
    second = np.asarray(slice_origin_lps(geometry, 1), dtype=float)
    step = second - first

    assert np.allclose(first, geometry.origin_lps)
    assert np.isclose(np.linalg.norm(step), geometry.spacing_xyz[2])
    assert np.allclose(step / np.linalg.norm(step), geometry.axis_k)


@pytest.mark.unit
def test_slice_origin_rejects_an_out_of_range_index():
    geometry = _geometry(size_xyz=(4, 4, 3))

    with pytest.raises(IndexError):
        slice_origin_lps(geometry, 3)


@pytest.mark.unit
def test_sort_orders_shuffled_slices_by_physical_position():
    geometry = _geometry()
    datasets = _dicom_series_from(geometry)
    shuffled = [datasets[2], datasets[0], datasets[3], datasets[1]]

    ordered = sort_dicom_series(shuffled)

    positions = [physical_slice_position(ds) for ds in ordered]
    assert positions == sorted(positions)
    assert [ds.InstanceNumber for ds in ordered] == [1, 2, 3, 4]


@pytest.mark.unit
def test_sort_rejects_mixed_orientations():
    geometry = _geometry()
    datasets = _dicom_series_from(geometry)
    datasets[2].ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]

    with pytest.raises(ValueError, match="inconsistent orientations"):
        sort_dicom_series(datasets)


@pytest.mark.unit
def test_geometry_from_dicom_rejects_duplicate_slice_positions():
    geometry = _geometry()
    datasets = _dicom_series_from(geometry)
    datasets[1].ImagePositionPatient = datasets[0].ImagePositionPatient

    with pytest.raises(ValueError):
        geometry_from_dicom_series(datasets)


@pytest.mark.unit
@pytest.mark.parametrize(
    "spacing",
    [(0.0, 1.0, 1.0), (-1.0, 1.0, 1.0), (float("nan"), 1.0, 1.0)],
)
def test_validate_rejects_unusable_spacing(spacing):
    with pytest.raises(ValueError, match="spacing"):
        validate_geometry(
            VolumeGeometry(
                size_xyz=(4, 4, 4),
                spacing_xyz=spacing,
                origin_lps=(0.0, 0.0, 0.0),
                direction=np.eye(3),
            )
        )


@pytest.mark.unit
def test_validate_rejects_a_degenerate_direction():
    collapsed = np.column_stack(
        (np.array([1.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0]))
    )

    with pytest.raises(ValueError):
        validate_geometry(
            VolumeGeometry(
                size_xyz=(4, 4, 4),
                spacing_xyz=(1.0, 1.0, 1.0),
                origin_lps=(0.0, 0.0, 0.0),
                direction=collapsed,
            )
        )


@pytest.mark.unit
def test_geometries_match_detects_a_sub_millimetre_shift():
    geometry = _geometry()
    shifted = VolumeGeometry(
        size_xyz=geometry.size_xyz,
        spacing_xyz=geometry.spacing_xyz,
        origin_lps=(
            geometry.origin_lps[0] + 0.5,
            geometry.origin_lps[1],
            geometry.origin_lps[2],
        ),
        direction=geometry.direction,
    )

    assert not geometries_match(geometry, shifted)
