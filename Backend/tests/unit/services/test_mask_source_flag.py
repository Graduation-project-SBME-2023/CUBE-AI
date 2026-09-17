"""The read-path flag and content-addressed publishing.

Both paths stay in place until the archived mask and the workspace copy have
been proved identical on real studies, so the flag is the whole migration
mechanism: one variable switches it, and reverting costs nothing.
"""

from __future__ import annotations

import numpy as np
import pytest

from services.mask_store import (
    LOCAL_FIRST,
    ORTHANC_FIRST,
    MaskStore,
    mask_source,
    orthanc_first,
)


@pytest.mark.unit
def test_the_workspace_copy_is_the_default(monkeypatch):
    monkeypatch.delenv("MASK_SOURCE", raising=False)

    assert mask_source() == LOCAL_FIRST
    assert orthanc_first() is False


@pytest.mark.unit
@pytest.mark.parametrize("value", ["orthanc", "ORTHANC", "  Orthanc  "])
def test_the_flag_switches_the_read_path(monkeypatch, value):
    monkeypatch.setenv("MASK_SOURCE", value)

    assert mask_source() == ORTHANC_FIRST
    assert orthanc_first() is True


@pytest.mark.unit
@pytest.mark.parametrize("value", ["local", "", "nonsense", "1"])
def test_anything_unrecognised_keeps_the_safe_default(monkeypatch, value):
    monkeypatch.setenv("MASK_SOURCE", value)

    assert orthanc_first() is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_second_edit_gets_its_own_series(tmp_path):
    """Two different edits of one target must not collide on a single UID.

    Publishing derives the UID from its inputs; without the mask content in
    that derivation the second edit would resolve to the first one's UID, be
    treated as already published, and never reach the archive.
    """
    from test_mask_store import FakeBackend, FakeOrthanc, _mask, _source_series, VOLUME_UID

    from imaging.dicom_seg import write_mask_nifti
    from imaging.geometry import geometry_from_dicom_series

    source = _source_series()
    geometry = geometry_from_dicom_series(source)
    orthanc = FakeOrthanc({VOLUME_UID: source})
    store = MaskStore(orthanc=orthanc, backend=FakeBackend([]))

    first_mask = _mask()
    second_mask = _mask()
    second_mask[3, 1:4, 1:3] = 1

    first = await store.publish(
        volume_uid=VOLUME_UID,
        target_label="kidney",
        mask_path=write_mask_nifti(first_mask, geometry, tmp_path / "one.nii.gz"),
        method="manual",
        derived_from="seg-ai",
    )
    second = await store.publish(
        volume_uid=VOLUME_UID,
        target_label="kidney",
        mask_path=write_mask_nifti(second_mask, geometry, tmp_path / "two.nii.gz"),
        method="manual",
        derived_from="seg-ai",
    )

    assert first.seg_series_uid != second.seg_series_uid
    assert len(orthanc.uploaded) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_republishing_identical_content_stays_a_no_op(tmp_path):
    from test_mask_store import FakeBackend, FakeOrthanc, _mask, _source_series, VOLUME_UID

    from imaging.dicom_seg import write_mask_nifti
    from imaging.geometry import geometry_from_dicom_series

    source = _source_series()
    geometry = geometry_from_dicom_series(source)
    orthanc = FakeOrthanc({VOLUME_UID: source})
    store = MaskStore(orthanc=orthanc, backend=FakeBackend([]))
    mask = _mask()

    first = await store.publish(
        volume_uid=VOLUME_UID,
        target_label="kidney",
        mask_path=write_mask_nifti(mask, geometry, tmp_path / "a.nii.gz"),
        method="manual",
    )
    # Same voxels written to a different file must still be one segmentation.
    second = await store.publish(
        volume_uid=VOLUME_UID,
        target_label="kidney",
        mask_path=write_mask_nifti(mask, geometry, tmp_path / "b.nii.gz"),
        method="manual",
    )

    assert first.seg_series_uid == second.seg_series_uid
    assert len(orthanc.uploaded) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publishing_from_an_array_matches_publishing_from_a_file(tmp_path):
    from test_mask_store import FakeBackend, FakeOrthanc, _mask, _source_series, VOLUME_UID

    from imaging.dicom_seg import write_mask_nifti
    from imaging.geometry import geometry_from_dicom_series

    source = _source_series()
    geometry = geometry_from_dicom_series(source)
    mask = _mask()

    from_file = await MaskStore(
        orthanc=FakeOrthanc({VOLUME_UID: source}), backend=FakeBackend([])
    ).publish(
        volume_uid=VOLUME_UID,
        target_label="kidney",
        mask_path=write_mask_nifti(mask, geometry, tmp_path / "a.nii.gz"),
        method="manual",
    )
    from_array = await MaskStore(
        orthanc=FakeOrthanc({VOLUME_UID: source}), backend=FakeBackend([])
    ).publish(
        volume_uid=VOLUME_UID,
        target_label="kidney",
        mask_array=np.asarray(mask),
        method="manual",
    )

    assert from_file.seg_series_uid == from_array.seg_series_uid
