"""DICOM SEG encode/decode, proved on a synthetic series.

The decode direction is new: before it existed, a mask could only be read back
by asking MONAI Label to re-export it, which is why the workspace copy and the
Orthanc copy could drift apart.
"""

from __future__ import annotations

import numpy as np
import pytest
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset

from imaging.dicom_seg import (
    MANUAL_DESCRIPTION_MARKER,
    SEG_GEOMETRY_VERSION,
    build_seg_dataset,
    deterministic_seg_uids,
    manual_series_description,
    mask_to_nifti_bytes,
    nifti_to_mask,
    seg_bytes,
    seg_to_mask_array,
    seg_to_nifti_bytes,
    write_mask_nifti,
)
from imaging.geometry import geometries_match, geometry_from_dicom_series
from ingestion.provenance import EXPLICIT_VR_LITTLE_ENDIAN

CT_SOP_CLASS = "1.2.840.10008.5.1.4.1.1.2"
ROWS, COLUMNS, SLICES = 8, 6, 4
SPACING_ROW_COL = [0.9, 0.7]
SLICE_SPACING = 2.5


def _source_series(oblique: bool = False) -> list[FileDataset]:
    """A minimal but geometrically complete CT series."""
    if oblique:
        angle = np.deg2rad(20.0)
        cos, sin = float(np.cos(angle)), float(np.sin(angle))
        orientation = [cos, sin, 0.0, -sin, cos, 0.0]
        normal = np.array([0.0, 0.0, 1.0])
    else:
        orientation = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        normal = np.array([0.0, 0.0, 1.0])

    study_uid = "1.2.3.4.5"
    series_uid = "1.2.3.4.6"
    origin = np.array([-50.0, -40.0, 10.0])

    datasets = []
    for index in range(SLICES):
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = CT_SOP_CLASS
        meta.MediaStorageSOPInstanceUID = f"1.2.3.4.7.{index}"
        meta.TransferSyntaxUID = EXPLICIT_VR_LITTLE_ENDIAN

        ds = FileDataset("", {}, file_meta=meta, preamble=b"\x00" * 128)
        ds.SOPClassUID = CT_SOP_CLASS
        ds.SOPInstanceUID = f"1.2.3.4.7.{index}"
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = series_uid
        ds.FrameOfReferenceUID = "1.2.3.4.8"
        ds.PatientID = "TEST-1"
        ds.PatientName = "TEST^ONE"
        ds.PatientBirthDate = ""
        ds.PatientSex = "O"
        ds.StudyDate = "20240101"
        ds.StudyTime = "120000"
        ds.StudyID = "1"
        ds.AccessionNumber = ""
        ds.ReferringPhysicianName = ""
        ds.Modality = "CT"
        ds.SeriesNumber = 1
        ds.InstanceNumber = index + 1
        ds.ImageOrientationPatient = orientation
        ds.ImagePositionPatient = (origin + index * SLICE_SPACING * normal).tolist()
        ds.PixelSpacing = SPACING_ROW_COL
        ds.SliceThickness = SLICE_SPACING
        ds.SpacingBetweenSlices = SLICE_SPACING
        ds.Rows = ROWS
        ds.Columns = COLUMNS
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 1
        ds.RescaleSlope = 1
        ds.RescaleIntercept = 0
        ds.PixelData = np.zeros((ROWS, COLUMNS), dtype=np.int16).tobytes()
        datasets.append(ds)
    return datasets


def _mask() -> np.ndarray:
    """An asymmetric mask, so a flip or transpose cannot pass unnoticed."""
    mask = np.zeros((SLICES, ROWS, COLUMNS), dtype=np.uint8)
    mask[1, 2:5, 1:3] = 1
    mask[2, 3:6, 2:4] = 1
    return mask


def _build(mask=None, source=None, method="auto", **kwargs):
    source = source or _source_series()
    series_uid, sop_uid = deterministic_seg_uids("job-1")
    return build_seg_dataset(
        mask_zyx=_mask() if mask is None else mask,
        source_images=source,
        segment_label="kidney",
        series_instance_uid=series_uid,
        sop_instance_uid=sop_uid,
        series_description="Segmentation - kidney",
        method=method,
        **kwargs,
    )


@pytest.mark.unit
def test_mask_survives_a_full_seg_round_trip():
    source = _source_series()
    mask = _mask()

    seg = _build(mask=mask, source=source)
    restored = seg_to_mask_array(seg, source)

    assert np.array_equal(restored, mask)


@pytest.mark.unit
def test_round_trip_holds_for_an_oblique_series():
    # Axis-aligned series hide orientation mistakes; oblique ones do not.
    source = _source_series(oblique=True)
    mask = _mask()

    restored = seg_to_mask_array(_build(mask=mask, source=source), source)

    assert np.array_equal(restored, mask)


@pytest.mark.unit
def test_decoding_tolerates_shuffled_source_instances():
    source = _source_series()
    mask = _mask()
    seg = _build(mask=mask, source=source)

    shuffled = [source[2], source[0], source[3], source[1]]
    restored = seg_to_mask_array(seg, shuffled)

    # Orthanc returns instances in no particular order, so decoding must sort.
    assert np.array_equal(restored, mask)


@pytest.mark.unit
def test_seg_inherits_the_source_frame_of_reference():
    source = _source_series()

    seg = _build(source=source)

    assert seg.FrameOfReferenceUID == source[0].FrameOfReferenceUID
    assert seg.StudyInstanceUID == source[0].StudyInstanceUID
    assert seg.Modality == "SEG"


@pytest.mark.unit
def test_uids_are_reproducible_for_the_same_job():
    first_series, first_sop = deterministic_seg_uids("job-42")
    second_series, second_sop = deterministic_seg_uids("job-42")

    assert first_series == second_series
    assert first_sop == second_sop
    assert first_series != first_sop
    assert deterministic_seg_uids("job-43")[0] != first_series


@pytest.mark.unit
def test_empty_masks_are_refused_rather_than_encoded():
    with pytest.raises(ValueError, match="no foreground"):
        _build(mask=np.zeros((SLICES, ROWS, COLUMNS), dtype=np.uint8))


@pytest.mark.unit
def test_a_mask_that_does_not_fit_the_series_is_refused():
    with pytest.raises(ValueError, match="does not match the source series"):
        _build(mask=np.ones((SLICES, ROWS + 1, COLUMNS), dtype=np.uint8))


@pytest.mark.unit
def test_manual_segmentations_are_marked_as_drawn_by_a_person():
    seg = _build(method="manual", derived_from_seg_uid="1.2.826.0.1.3680043.10.511.3.99")
    segment = seg.SegmentSequence[0]

    assert segment.SegmentAlgorithmType == "MANUAL"
    # highdicom forbids algorithm identification on a manual segment, and the
    # distinction is real: a clinician drew this, not a model.
    assert "SegmentationAlgorithmIdentificationSequence" not in segment
    assert "1.2.826.0.1.3680043.10.511.3.99" in seg.DerivationDescription


@pytest.mark.unit
def test_automatic_segmentations_record_the_model():
    seg = _build(method="auto", model_name="voxtell")
    segment = seg.SegmentSequence[0]

    assert segment.SegmentAlgorithmType == "AUTOMATIC"
    assert seg.SoftwareVersions == SEG_GEOMETRY_VERSION


@pytest.mark.unit
def test_manual_description_is_recognised_by_the_database_sync():
    # application_api derives method="manual" from this word in the description.
    description = manual_series_description("kidney")

    assert MANUAL_DESCRIPTION_MARKER in description.upper()
    assert len(description) <= 64
    # Already-marked descriptions must not be marked twice.
    assert manual_series_description("kidney", description) == description


@pytest.mark.unit
def test_seg_decodes_into_nifti_with_the_source_geometry(tmp_path):
    source = _source_series()
    mask = _mask()
    seg = _build(mask=mask, source=source)

    payload = seg_to_nifti_bytes(seg, source)
    target = tmp_path / "mask.nii.gz"
    target.write_bytes(payload)
    restored, geometry = nifti_to_mask(target)

    assert np.array_equal(restored, mask)
    assert geometries_match(geometry, geometry_from_dicom_series(source))


@pytest.mark.unit
def test_writing_a_mask_nifti_preserves_placement(tmp_path):
    source = _source_series()
    geometry = geometry_from_dicom_series(source)
    mask = _mask()

    path = write_mask_nifti(mask, geometry, tmp_path / "nested" / "mask.nii.gz")
    restored, restored_geometry = nifti_to_mask(path)

    assert path.is_file()
    assert np.array_equal(restored, mask)
    assert geometries_match(geometry, restored_geometry)


@pytest.mark.unit
def test_mask_to_nifti_bytes_matches_the_file_writer(tmp_path):
    geometry = geometry_from_dicom_series(_source_series())
    mask = _mask()

    payload = mask_to_nifti_bytes(mask, geometry)
    target = tmp_path / "mask.nii.gz"
    target.write_bytes(payload)

    assert np.array_equal(nifti_to_mask(target)[0], mask)


@pytest.mark.unit
def test_seg_serialises_to_a_readable_dicom_file():
    import io

    import pydicom

    seg = _build()

    reread = pydicom.dcmread(io.BytesIO(seg_bytes(seg)))

    assert reread.SOPInstanceUID == seg.SOPInstanceUID
    assert reread.Modality == "SEG"


@pytest.mark.unit
def test_encoding_needs_source_images():
    with pytest.raises(ValueError, match="source images"):
        build_seg_dataset(
            mask_zyx=_mask(),
            source_images=[],
            segment_label="kidney",
            series_instance_uid="1.2.3",
            sop_instance_uid="1.2.4",
            series_description="x",
        )


@pytest.mark.unit
def test_geometry_version_matches_the_segmentation_pipeline():
    # A drift here would silently split masks into two geometry generations.
    from agents.execution_agents.segmentation.utils import SEGMENTATION_GEOMETRY_VERSION

    assert SEG_GEOMETRY_VERSION == SEGMENTATION_GEOMETRY_VERSION
