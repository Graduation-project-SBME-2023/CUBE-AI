"""End-to-end conversion tests that never touch Orthanc or MONAI.

The conversion is a pure function, so the geometry and the pixel rescale can be
proved with a small synthetic volume written to ``tmp_path``.
"""

from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk

from imaging.geometry import (
    geometries_match,
    geometry_from_dicom_series,
    geometry_from_sitk,
)
from ingestion.nifti_to_dicom import (
    SeriesIdentity,
    convert_volume_to_dicom,
    datasets_to_array,
    file_checksum,
    rescale_to_int16,
    synthetic_series_identity,
)
from ingestion.provenance import (
    CLINICAL_TRIAL_PROTOCOL_ID,
    NIFTI_UID_ROOT,
    PRIVATE_CREATOR,
    PRIVATE_GROUP,
    is_ingested_uid,
    sop_class_for_modality,
)

SPACING_XYZ = (0.8, 1.1, 3.0)
ORIGIN_LPS = (-95.5, -60.25, 12.0)


def _oblique_direction() -> np.ndarray:
    angle = np.deg2rad(17.0)
    cos, sin = np.cos(angle), np.sin(angle)
    axis_i = np.array([cos, sin, 0.0])
    axis_k = np.array([-sin * 0.2, cos * 0.2, 1.0])
    axis_k = axis_k / np.linalg.norm(axis_k)
    axis_j = np.cross(axis_k, axis_i)
    axis_j = axis_j / np.linalg.norm(axis_j)
    axis_k = np.cross(axis_i, axis_j)
    return np.column_stack((axis_i, axis_j, axis_k))


def _write_volume(tmp_path, array_zyx, *, name="scan.nii.gz"):
    image = sitk.GetImageFromArray(np.ascontiguousarray(array_zyx))
    image.SetSpacing(SPACING_XYZ)
    image.SetOrigin(ORIGIN_LPS)
    image.SetDirection(tuple(_oblique_direction().flatten(order="C")))
    path = tmp_path / name
    sitk.WriteImage(image, str(path))
    return path


def _hu_volume(shape=(4, 5, 6)) -> np.ndarray:
    values = np.linspace(-1000, 1200, int(np.prod(shape)))
    return values.reshape(shape).round().astype(np.int16)


@pytest.mark.unit
def test_conversion_preserves_physical_geometry(tmp_path):
    array = _hu_volume()
    path = _write_volume(tmp_path, array)
    checksum = file_checksum(path)

    result = convert_volume_to_dicom(
        path,
        modality="CT",
        identity=synthetic_series_identity(checksum),
        checksum=checksum,
    )

    written = geometry_from_sitk(sitk.ReadImage(str(path)))
    from_dicom = geometry_from_dicom_series(result.datasets)

    assert geometries_match(written, from_dicom)
    assert len(result.datasets) == array.shape[0]


@pytest.mark.unit
def test_hounsfield_volume_round_trips_voxel_for_voxel(tmp_path):
    array = _hu_volume()
    path = _write_volume(tmp_path, array)

    result = convert_volume_to_dicom(
        path,
        modality="CT",
        identity=synthetic_series_identity(file_checksum(path)),
    )

    assert result.rescale_type == "HU"
    assert result.rescale_slope == 1.0
    assert np.array_equal(datasets_to_array(result.datasets), array.astype(np.float64))


@pytest.mark.unit
def test_float_volume_is_rescaled_and_claims_no_units(tmp_path):
    array = np.linspace(0.0, 1.0, 4 * 5 * 6).reshape((4, 5, 6)).astype(np.float32)
    path = _write_volume(tmp_path, array)

    result = convert_volume_to_dicom(
        path,
        modality="OT",
        identity=synthetic_series_identity(file_checksum(path)),
    )

    # No RescaleType: the stored data is an approximation with unknown units.
    assert result.rescale_type is None
    assert any("rescaled linearly" in warning for warning in result.warnings)
    restored = datasets_to_array(result.datasets)
    assert np.allclose(restored, array, atol=(array.max() - array.min()) / 60000.0)


@pytest.mark.unit
def test_uniform_volume_does_not_divide_by_zero(tmp_path):
    array = np.full((3, 4, 4), 7.5, dtype=np.float32)
    path = _write_volume(tmp_path, array)

    result = convert_volume_to_dicom(
        path,
        modality="OT",
        identity=synthetic_series_identity(file_checksum(path)),
    )

    assert np.allclose(datasets_to_array(result.datasets), 7.5)


@pytest.mark.unit
def test_pixel_spacing_is_not_transposed(tmp_path):
    path = _write_volume(tmp_path, _hu_volume())

    result = convert_volume_to_dicom(
        path,
        modality="CT",
        identity=synthetic_series_identity(file_checksum(path)),
    )

    # SimpleITK spacing is (x, y, z); DICOM PixelSpacing is [row, column].
    # NIfTI stores spacing as float32, so compare with a tolerance rather than
    # against the float64 literals used to write the file.
    spacing = [float(value) for value in result.datasets[0].PixelSpacing]
    assert spacing == pytest.approx([SPACING_XYZ[1], SPACING_XYZ[0]], rel=1e-6)
    assert float(result.datasets[0].SliceThickness) == pytest.approx(
        SPACING_XYZ[2], rel=1e-6
    )


@pytest.mark.unit
def test_every_slice_shares_one_series_and_frame_of_reference(tmp_path):
    path = _write_volume(tmp_path, _hu_volume())

    result = convert_volume_to_dicom(
        path,
        modality="CT",
        identity=synthetic_series_identity(file_checksum(path)),
    )

    assert {ds.SeriesInstanceUID for ds in result.datasets} == {
        result.series_instance_uid
    }
    assert {ds.FrameOfReferenceUID for ds in result.datasets} == {
        result.frame_of_reference_uid
    }
    assert len({ds.SOPInstanceUID for ds in result.datasets}) == len(result.datasets)
    assert [ds.InstanceNumber for ds in result.datasets] == [1, 2, 3, 4]


@pytest.mark.unit
def test_same_file_converts_to_the_same_uids(tmp_path):
    # Re-uploading a file must resolve to one patient, not a duplicate.
    array = _hu_volume()
    first_path = _write_volume(tmp_path, array, name="scan.nii.gz")
    second_path = _write_volume(tmp_path, array, name="copy.nii.gz")
    checksum = file_checksum(first_path)

    first = convert_volume_to_dicom(
        first_path, modality="CT", identity=synthetic_series_identity(checksum)
    )
    second = convert_volume_to_dicom(
        second_path,
        modality="CT",
        identity=synthetic_series_identity(checksum),
        checksum=checksum,
    )

    assert first.series_instance_uid == second.series_instance_uid
    assert [ds.SOPInstanceUID for ds in first.datasets] == [
        ds.SOPInstanceUID for ds in second.datasets
    ]
    assert first.datasets[0].PatientID == second.datasets[0].PatientID


@pytest.mark.unit
def test_converted_series_is_marked_as_derived(tmp_path):
    path = _write_volume(tmp_path, _hu_volume())

    result = convert_volume_to_dicom(
        path,
        modality="CT",
        identity=synthetic_series_identity(file_checksum(path)),
    )
    dataset = result.datasets[0]

    assert list(dataset.ImageType)[:2] == ["DERIVED", "SECONDARY"]
    assert dataset.ConversionType == "WSD"
    assert dataset.PatientIdentityRemoved == "YES"
    assert dataset.ClinicalTrialProtocolID == CLINICAL_TRIAL_PROTOCOL_ID
    assert is_ingested_uid(dataset.SeriesInstanceUID)
    assert dataset.SeriesInstanceUID.startswith(NIFTI_UID_ROOT)


@pytest.mark.unit
def test_series_description_names_the_format_and_fits_the_lo_limit(tmp_path):
    long_name = "a" * 90 + ".nii.gz"
    path = _write_volume(tmp_path, _hu_volume(), name=long_name)

    result = convert_volume_to_dicom(
        path,
        modality="CT",
        identity=synthetic_series_identity(file_checksum(path)),
    )
    description = str(result.datasets[0].SeriesDescription)

    assert description.startswith("[NIfTI] ")
    assert len(description) <= 64


@pytest.mark.unit
def test_private_block_records_the_source_file(tmp_path):
    path = _write_volume(tmp_path, _hu_volume(), name="kidney_case_7.nii.gz")
    checksum = file_checksum(path)

    result = convert_volume_to_dicom(
        path,
        modality="CT",
        identity=synthetic_series_identity(checksum),
        checksum=checksum,
    )
    block = result.datasets[0].private_block(PRIVATE_GROUP, PRIVATE_CREATOR)

    assert block[0x01].value == ".nii.gz"
    assert block[0x02].value == "kidney_case_7.nii.gz"
    assert block[0x03].value == checksum
    assert block[0x07].value == "CT"


@pytest.mark.unit
def test_modality_selects_the_sop_class(tmp_path):
    path = _write_volume(tmp_path, _hu_volume())
    identity = synthetic_series_identity(file_checksum(path))

    for modality in ("CT", "MR", "OT"):
        result = convert_volume_to_dicom(path, modality=modality, identity=identity)
        expected = sop_class_for_modality(modality)
        assert result.datasets[0].SOPClassUID == expected
        assert result.datasets[0].file_meta.MediaStorageSOPClassUID == expected
        assert result.datasets[0].Modality == modality


@pytest.mark.unit
def test_unknown_modality_is_rejected_rather_than_guessed(tmp_path):
    path = _write_volume(tmp_path, _hu_volume())

    with pytest.raises(ValueError, match="Unsupported modality"):
        convert_volume_to_dicom(
            path,
            modality="ULTRASOUND",
            identity=synthetic_series_identity(file_checksum(path)),
        )


@pytest.mark.unit
def test_unsupported_extension_is_rejected(tmp_path):
    path = tmp_path / "scan.dcm"
    path.write_bytes(b"not a volume")

    with pytest.raises(ValueError, match="Unsupported file type"):
        convert_volume_to_dicom(
            path,
            modality="CT",
            identity=synthetic_series_identity("a" * 64),
            checksum="a" * 64,
        )


@pytest.mark.unit
def test_series_can_be_attached_to_an_existing_study(tmp_path):
    path = _write_volume(tmp_path, _hu_volume())
    identity = SeriesIdentity(
        patient_id="REAL-123",
        patient_name="DOE^JANE",
        patient_birth_date="19700101",
        patient_sex="F",
        study_instance_uid="1.2.840.113619.2.55.3.999",
        study_date="20240115",
        study_description="Abdomen CT",
    )

    result = convert_volume_to_dicom(path, modality="CT", identity=identity)
    dataset = result.datasets[0]

    assert dataset.StudyInstanceUID == identity.study_instance_uid
    assert dataset.PatientID == "REAL-123"
    # The ingested grid is not proven to share physical space with the existing
    # series, so it must not inherit that study's frame of reference.
    assert is_ingested_uid(dataset.FrameOfReferenceUID)


@pytest.mark.unit
def test_non_finite_voxels_are_replaced_with_a_warning():
    array = np.array([[[1.0, 2.0], [np.nan, 4.5]]], dtype=np.float32)

    stored, _slope, _intercept, rescale_type, warnings = rescale_to_int16(array)

    assert np.all(np.isfinite(stored))
    assert rescale_type is None
    assert any("non-finite" in warning for warning in warnings)


@pytest.mark.unit
def test_hounsfield_units_are_only_claimed_for_ct(tmp_path):
    # Float voxels that happen to be integral do not make an MR volume
    # Hounsfield. The values still pass through unchanged.
    array = _hu_volume().astype(np.float32)
    path = _write_volume(tmp_path, array)
    identity = synthetic_series_identity(file_checksum(path))

    as_ct = convert_volume_to_dicom(path, modality="CT", identity=identity)
    as_mr = convert_volume_to_dicom(path, modality="MR", identity=identity)

    assert as_ct.rescale_type == "HU"
    assert as_mr.rescale_type is None
    assert "RescaleType" not in as_mr.datasets[0]
    assert np.array_equal(
        datasets_to_array(as_mr.datasets), array.astype(np.float64)
    )


@pytest.mark.unit
def test_multi_component_volumes_are_rejected(tmp_path):
    # SimpleITK reads a trailing 4th axis as vector-valued voxels rather than a
    # fourth dimension; either way the volume is not a single scalar grid.
    image = sitk.GetImageFromArray(np.zeros((2, 3, 4, 5), dtype=np.int16))
    path = tmp_path / "timeseries.nii.gz"
    sitk.WriteImage(image, str(path))

    with pytest.raises(ValueError, match="single-channel"):
        convert_volume_to_dicom(
            path,
            modality="CT",
            identity=synthetic_series_identity("b" * 64),
            checksum="b" * 64,
        )
