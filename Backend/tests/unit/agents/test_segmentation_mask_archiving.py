"""The segmentation module's two mask-storage seams.

Publishing a reviewed mask closes a real gap: an edited mask used to be copied
over the workspace NIfTI and nowhere else, so Orthanc kept the model's version
while radiomics ran on the clinician's. Reading is behind MASK_SOURCE, because
the archived mask and the workspace copy still have to be proved identical.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.execution_agents.segmentation import module as segmentation_module
from agents.execution_agents.segmentation.module import SegmentationModule
from services.mask_store import SegRef


class RecordingStore:
    """Stands in for MaskStore; records what the module asked it to do."""

    published: list[dict] = []
    materialised: list[tuple[str, str]] = []
    publish_error: Exception | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc_info):
        return None

    async def publish(self, **kwargs):
        if RecordingStore.publish_error:
            raise RecordingStore.publish_error
        RecordingStore.published.append(kwargs)
        return SegRef(
            seg_series_uid="seg-manual-1",
            volume_uid=kwargs["volume_uid"],
            target_label=kwargs["target_label"],
            method="manual",
        )

    async def ensure_local(self, session_id, ref):
        RecordingStore.materialised.append((session_id, ref.seg_series_uid))
        return Path(f"/cache/{ref.seg_series_uid}.nii.gz")


@pytest.fixture(autouse=True)
def recording_store(monkeypatch):
    RecordingStore.published = []
    RecordingStore.materialised = []
    RecordingStore.publish_error = None
    monkeypatch.setattr(segmentation_module, "MaskStore", RecordingStore)
    return RecordingStore


def _module() -> SegmentationModule:
    return SegmentationModule(session_id="sess-1")


def _item(**overrides):
    item = {
        "pair_id": "1.2.3__t1",
        "volume_uid": "1.2.3",
        "target_id": "t1",
        "canonical_label": "kidney",
        "inference_result": {"segmentation_series_uid": "seg-auto-1"},
    }
    item.update(overrides)
    return item


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_edited_mask_is_archived_against_the_model_output(tmp_path):
    item = _item()
    edit = tmp_path / "edited.nii.gz"
    edit.write_bytes(b"nifti")

    await _module()._publish_reviewed_mask(item, edit)

    assert item["orthanc_publish"] == "ok"
    assert item["manual_segmentation_series_uid"] == "seg-manual-1"
    call = RecordingStore.published[0]
    assert call["method"] == "manual"
    assert call["volume_uid"] == "1.2.3"
    assert call["target_label"] == "kidney"
    # The edit must stay linked to the segmentation it was corrected from.
    assert call["derived_from"] == "seg-auto-1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_failed_archive_is_recorded_but_never_stops_the_run(tmp_path):
    RecordingStore.publish_error = RuntimeError("orthanc is down")
    item = _item()
    edit = tmp_path / "edited.nii.gz"
    edit.write_bytes(b"nifti")

    await _module()._publish_reviewed_mask(item, edit)

    # Worst case is the behaviour that existed before -- but now it is visible.
    assert item["orthanc_publish"] == "failed"
    assert "orthanc is down" in item["orthanc_publish_error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_archiving_is_skipped_when_the_pair_has_no_target(tmp_path):
    item = _item(canonical_label=None, target_id=None)
    edit = tmp_path / "edited.nii.gz"
    edit.write_bytes(b"nifti")

    await _module()._publish_reviewed_mask(item, edit)

    assert item["orthanc_publish"] == "skipped"
    assert RecordingStore.published == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_archiving_happens_even_with_the_read_flag_off(monkeypatch, tmp_path):
    # Writing to the archive is additive, so it is not behind the flag: it fills
    # a gap rather than replacing a working path.
    monkeypatch.setenv("MASK_SOURCE", "local")
    item = _item()
    edit = tmp_path / "edited.nii.gz"
    edit.write_bytes(b"nifti")

    await _module()._publish_reviewed_mask(item, edit)

    assert item["orthanc_publish"] == "ok"
    # But downstream keeps reading the workspace copy.
    assert "mask_path" not in item


@pytest.mark.unit
@pytest.mark.asyncio
async def test_with_the_flag_on_downstream_follows_the_archived_edit(monkeypatch, tmp_path):
    monkeypatch.setenv("MASK_SOURCE", "orthanc")
    item = _item()
    edit = tmp_path / "edited.nii.gz"
    edit.write_bytes(b"nifti")

    await _module()._publish_reviewed_mask(item, edit)

    assert item["mask_path"] == "mask_cache/seg-manual-1.nii.gz"
    assert ("sess-1", "seg-manual-1") in RecordingStore.materialised


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_workspace_copy_is_used_by_default(monkeypatch):
    monkeypatch.delenv("MASK_SOURCE", raising=False)

    path = await _module()._resolve_mask_path(_item(), "abc123")

    assert path == "masks/abc123.nii.gz"
    assert RecordingStore.materialised == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_flag_points_previews_and_radiomics_at_the_archive(monkeypatch):
    monkeypatch.setenv("MASK_SOURCE", "orthanc")

    path = await _module()._resolve_mask_path(_item(), "abc123")

    assert path == "mask_cache/seg-auto-1.nii.gz"
    assert ("sess-1", "seg-auto-1") in RecordingStore.materialised


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_pair_with_no_archived_mask_falls_back(monkeypatch):
    monkeypatch.setenv("MASK_SOURCE", "orthanc")
    item = _item(inference_result={})

    path = await _module()._resolve_mask_path(item, "abc123")

    assert path == "masks/abc123.nii.gz"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_unreadable_archive_falls_back_instead_of_failing(monkeypatch):
    monkeypatch.setenv("MASK_SOURCE", "orthanc")

    async def _boom(self, session_id, ref):
        raise RuntimeError("orthanc is down")

    monkeypatch.setattr(RecordingStore, "ensure_local", _boom)

    path = await _module()._resolve_mask_path(_item(), "abc123")

    assert path == "masks/abc123.nii.gz"
