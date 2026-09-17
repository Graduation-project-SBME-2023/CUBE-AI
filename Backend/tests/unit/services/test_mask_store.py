"""MaskStore behaviour against a fake Orthanc and a fake Application API.

The properties that matter are the ones that stop the archive and the workspace
from disagreeing: a clinician's edit wins over the model's output, publishing an
edit adds a series instead of replacing one, and a mask whose geometry does not
match the source is refused rather than archived.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest
from pydicom.dataset import FileDataset, FileMetaDataset

from imaging.dicom_seg import build_seg_dataset, deterministic_seg_uids, write_mask_nifti
from imaging.geometry import geometry_from_dicom_series
from ingestion.provenance import EXPLICIT_VR_LITTLE_ENDIAN
from services.mask_store import MaskStore, SegRef

CT_SOP_CLASS = "1.2.840.10008.5.1.4.1.1.2"
VOLUME_UID = "1.2.3.4.6"
ROWS, COLUMNS, SLICES = 8, 6, 4
SLICE_SPACING = 2.5


def _source_series() -> list[FileDataset]:
    datasets = []
    for index in range(SLICES):
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = CT_SOP_CLASS
        meta.MediaStorageSOPInstanceUID = f"1.2.3.4.7.{index}"
        meta.TransferSyntaxUID = EXPLICIT_VR_LITTLE_ENDIAN

        ds = FileDataset("", {}, file_meta=meta, preamble=b"\x00" * 128)
        ds.SOPClassUID = CT_SOP_CLASS
        ds.SOPInstanceUID = f"1.2.3.4.7.{index}"
        ds.StudyInstanceUID = "1.2.3.4.5"
        ds.SeriesInstanceUID = VOLUME_UID
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
        ds.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        ds.ImagePositionPatient = [-50.0, -40.0, 10.0 + index * SLICE_SPACING]
        ds.PixelSpacing = [0.9, 0.7]
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
    mask = np.zeros((SLICES, ROWS, COLUMNS), dtype=np.uint8)
    mask[1, 2:5, 1:3] = 1
    mask[2, 3:6, 2:4] = 1
    return mask


class FakeOrthanc:
    def __init__(self, series: dict[str, list] | None = None):
        self.series = series or {}
        self.uploaded: list = []

    async def read_series_datasets(self, series_uid, stop_before_pixels=False):
        return list(self.series.get(series_uid, []))

    async def find_series(self, series_uid):
        return f"orthanc-{series_uid}" if series_uid in self.series else None

    async def upload_dataset(self, dataset):
        self.uploaded.append(dataset)
        self.series.setdefault(str(dataset.SeriesInstanceUID), []).append(dataset)
        return "instance-1"

    async def close(self):
        return None


class FakeBackend:
    def __init__(self, records):
        self.records = records

    async def get_segmentations_by_volume(self, volume_uid):
        return [r for r in self.records if r["volume_series_instance_uid"] == volume_uid]


def _record(seg_uid, method, created_at, target="kidney"):
    return {
        "label_series_instance_uid": seg_uid,
        "volume_series_instance_uid": VOLUME_UID,
        "target_label": target,
        "method": method,
        "created_at": created_at,
        "orthanc_series_id": f"orthanc-{seg_uid}",
    }


def _store(orthanc=None, records=()):
    return MaskStore(orthanc=orthanc or FakeOrthanc(), backend=FakeBackend(list(records)))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_clinician_edit_wins_over_the_model_output():
    store = _store(
        records=[
            _record("seg-manual", "manual", "2024-01-01T09:00:00"),
            # The automatic mask is newer, but a person reviewed the other one.
            _record("seg-auto", "auto", "2024-06-01T09:00:00"),
        ]
    )

    ref = await store.resolve(VOLUME_UID)

    assert ref.seg_series_uid == "seg-manual"
    assert ref.is_manual


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_newest_automatic_mask_is_used_when_none_were_edited():
    store = _store(
        records=[
            _record("seg-old", "auto", "2024-01-01T09:00:00"),
            _record("seg-new", "auto", "2024-06-01T09:00:00"),
        ]
    )

    ref = await store.resolve(VOLUME_UID)

    assert ref.seg_series_uid == "seg-new"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_masks_can_be_narrowed_to_one_target():
    store = _store(
        records=[
            _record("seg-kidney", "auto", "2024-01-01T09:00:00", target="kidney"),
            _record("seg-liver", "auto", "2024-02-01T09:00:00", target="liver"),
        ]
    )

    ref = await store.resolve(VOLUME_UID, target_label="liver")

    assert ref.seg_series_uid == "seg-liver"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_returns_none_when_a_volume_has_no_mask():
    assert await _store().resolve(VOLUME_UID) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_records_without_a_timestamp_do_not_crash_selection():
    store = _store(
        records=[
            _record("seg-undated", "auto", None),
            _record("seg-dated", "auto", "2024-01-01T09:00:00"),
        ]
    )

    ref = await store.resolve(VOLUME_UID)

    assert ref.seg_series_uid == "seg-dated"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ensure_local_materialises_the_mask_from_orthanc(tmp_path):
    source = _source_series()
    mask = _mask()
    series_uid, sop_uid = deterministic_seg_uids("job-1")
    seg = build_seg_dataset(
        mask_zyx=mask,
        source_images=source,
        segment_label="kidney",
        series_instance_uid=series_uid,
        sop_instance_uid=sop_uid,
        series_description="Segmentation - kidney",
    )
    orthanc = FakeOrthanc({VOLUME_UID: source, series_uid: [seg]})
    store = _store(orthanc)
    ref = SegRef(seg_series_uid=series_uid, volume_uid=VOLUME_UID, target_label="kidney")

    path = await store.ensure_local("sess-1", ref)

    assert path.is_file()
    # The cache lives under cache/, the only tree a teardown may delete.
    assert path.parent.parent.name == "cache"
    from imaging.dicom_seg import nifti_to_mask

    assert np.array_equal(nifti_to_mask(path)[0], mask)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ensure_local_reuses_the_cached_file():
    source = _source_series()
    series_uid, sop_uid = deterministic_seg_uids("job-2")
    seg = build_seg_dataset(
        mask_zyx=_mask(),
        source_images=source,
        segment_label="kidney",
        series_instance_uid=series_uid,
        sop_instance_uid=sop_uid,
        series_description="Segmentation - kidney",
    )
    orthanc = FakeOrthanc({VOLUME_UID: source, series_uid: [seg]})
    store = _store(orthanc)
    ref = SegRef(seg_series_uid=series_uid, volume_uid=VOLUME_UID)

    first = await store.ensure_local("sess-2", ref)
    marker = first.stat().st_mtime_ns
    second = await store.ensure_local("sess-2", ref)

    assert second == first
    assert second.stat().st_mtime_ns == marker


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publishing_an_edit_adds_a_series_and_keeps_the_original(tmp_path):
    source = _source_series()
    geometry = geometry_from_dicom_series(source)
    original_uid, original_sop = deterministic_seg_uids("job-3")
    original = build_seg_dataset(
        mask_zyx=_mask(),
        source_images=source,
        segment_label="kidney",
        series_instance_uid=original_uid,
        sop_instance_uid=original_sop,
        series_description="Segmentation - kidney",
    )
    orthanc = FakeOrthanc({VOLUME_UID: source, original_uid: [original]})
    store = _store(orthanc)

    edited = _mask()
    edited[2, 3:6, 2:4] = 0
    edited[3, 1:4, 1:3] = 1
    path = write_mask_nifti(edited, geometry, tmp_path / "edited.nii.gz")

    ref = await store.publish(
        volume_uid=VOLUME_UID,
        target_label="kidney",
        mask_path=path,
        method="manual",
        derived_from=original_uid,
    )

    assert ref.method == "manual"
    assert ref.seg_series_uid != original_uid
    # The model's mask must survive the edit.
    assert original_uid in orthanc.series
    published = orthanc.uploaded[0]
    assert "MANUAL" in str(published.SeriesDescription).upper()
    assert original_uid in published.DerivationDescription


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_published_edit_reads_back_identically(tmp_path):
    source = _source_series()
    geometry = geometry_from_dicom_series(source)
    orthanc = FakeOrthanc({VOLUME_UID: source})
    store = _store(orthanc)

    edited = _mask()
    path = write_mask_nifti(edited, geometry, tmp_path / "edited.nii.gz")
    ref = await store.publish(
        volume_uid=VOLUME_UID, target_label="kidney", mask_path=path, method="manual"
    )

    restored, _ = await store.read_mask(ref)

    assert np.array_equal(restored, edited)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publishing_the_same_mask_twice_does_not_duplicate_it(tmp_path):
    source = _source_series()
    geometry = geometry_from_dicom_series(source)
    orthanc = FakeOrthanc({VOLUME_UID: source})
    store = _store(orthanc)
    path = write_mask_nifti(_mask(), geometry, tmp_path / "edited.nii.gz")

    first = await store.publish(
        volume_uid=VOLUME_UID, target_label="kidney", mask_path=path, method="manual"
    )
    second = await store.publish(
        volume_uid=VOLUME_UID, target_label="kidney", mask_path=path, method="manual"
    )

    assert first.seg_series_uid == second.seg_series_uid
    assert len(orthanc.uploaded) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_mask_on_the_wrong_grid_is_refused(tmp_path):
    from imaging.geometry import VolumeGeometry

    source = _source_series()
    orthanc = FakeOrthanc({VOLUME_UID: source})
    store = _store(orthanc)
    # Same shape, different physical placement -- archiving this would be a
    # segmentation of the wrong voxels.
    wrong = VolumeGeometry(
        size_xyz=(COLUMNS, ROWS, SLICES),
        spacing_xyz=(0.7, 0.9, SLICE_SPACING),
        origin_lps=(999.0, -40.0, 10.0),
        direction=np.eye(3),
    )
    path = write_mask_nifti(_mask(), wrong, tmp_path / "wrong.nii.gz")

    with pytest.raises(ValueError, match="does not share the geometry"):
        await store.publish(
            volume_uid=VOLUME_UID, target_label="kidney", mask_path=path, method="manual"
        )
    assert orthanc.uploaded == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publish_requires_exactly_one_mask_source(tmp_path):
    store = _store(FakeOrthanc({VOLUME_UID: _source_series()}))

    with pytest.raises(ValueError, match="exactly one"):
        await store.publish(volume_uid=VOLUME_UID, target_label="kidney")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_clearing_a_session_removes_only_the_cache():
    source = _source_series()
    series_uid, sop_uid = deterministic_seg_uids("job-4")
    seg = build_seg_dataset(
        mask_zyx=_mask(),
        source_images=source,
        segment_label="kidney",
        series_instance_uid=series_uid,
        sop_instance_uid=sop_uid,
        series_description="Segmentation - kidney",
    )
    orthanc = FakeOrthanc({VOLUME_UID: source, series_uid: [seg]})
    store = _store(orthanc)
    ref = SegRef(seg_series_uid=series_uid, volume_uid=VOLUME_UID)
    path = await store.ensure_local("sess-clear", ref)

    removed = await store.clear_session("sess-clear")

    assert removed == 1
    assert not path.exists()
    # Orthanc keeps the archive; only the disposable copy went away.
    assert series_uid in orthanc.series
    # And it can be fetched again on demand.
    assert (await store.ensure_local("sess-clear", ref)).is_file()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_clearing_a_session_with_no_cache_is_harmless():
    assert await _store().clear_session("sess-never-used") == 0


@pytest.mark.unit
def test_sweeping_removes_only_caches_past_the_ttl():
    import os
    import time

    from storage_paths import ws_sub

    fresh = ws_sub("sweep-fresh", "mask_cache")
    stale = ws_sub("sweep-stale", "mask_cache")
    for directory in (fresh, stale):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "a.nii.gz").write_bytes(b"x")
    old = time.time() - 100_000
    os.utime(stale, (old, old))

    removed = MaskStore.sweep_stale_caches(ttl_seconds=3600)

    assert removed >= 1
    assert not stale.exists()
    assert fresh.exists()
