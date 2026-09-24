"""Synthetic geometry regressions for fresh and reused segmentation masks."""

from pathlib import Path

import httpx
import numpy as np
import pydicom
import pytest
import SimpleITK as sitk

from agents.execution_agents.segmentation import utils
from storage_paths import workspace_subpath


def _mask_on_grid(size, point, flipped_axis=None):
    data = np.zeros((size[2], size[1], size[0]), dtype=np.uint8)
    index = list(point)
    if flipped_axis is not None:
        index[flipped_axis] = size[flipped_axis] - 1 - index[flipped_axis]
    data[index[2], index[1], index[0]] = 1
    image = sitk.GetImageFromArray(data)
    if flipped_axis is not None:
        origin = [0.0, 0.0, 0.0]
        origin[flipped_axis] = float(size[flipped_axis] - 1)
        direction = np.eye(3)
        direction[flipped_axis, flipped_axis] = -1
        image.SetOrigin(origin)
        image.SetDirection(tuple(direction.flatten()))
    return image


@pytest.mark.unit
@pytest.mark.parametrize("flipped_axis", [0, 1, 2])
def test_same_size_mask_resamples_to_each_reference_plane(tmp_path, flipped_axis):
    size = (4, 3, 5)
    point = (1, 0, 1)
    reference_path = tmp_path / "volume.nii.gz"
    mask_path = tmp_path / "mask.nii.gz"
    output_path = tmp_path / "aligned.nii.gz"
    sitk.WriteImage(sitk.Image(size, sitk.sitkInt16), str(reference_path))
    sitk.WriteImage(_mask_on_grid(size, point, flipped_axis), str(mask_path))

    result = utils.align_and_binarize_mask(
        str(mask_path), str(reference_path), str(output_path)
    )
    aligned = sitk.GetArrayFromImage(sitk.ReadImage(str(output_path)))

    assert result["alignment_mode"] == "physical_resample"
    assert int(aligned.sum()) == 1
    assert int(aligned[point[2], point[1], point[0]]) == 1


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("reused", [False, True])
async def test_reused_mask_reads_archive_while_fresh_mask_reads_export(
    tmp_path, monkeypatch, reused
):
    size = (4, 3, 5)
    point = (1, 0, 1)
    volume_dir = workspace_subpath(tmp_path, "volumes")
    volume_dir.mkdir(parents=True)
    sitk.WriteImage(
        sitk.Image(size, sitk.sitkInt16),
        str(volume_dir / "volume-uid.nii.gz"),
    )

    label_dir = tmp_path / "monai-label-cache"
    label_dir.mkdir()
    archive_path = tmp_path / "archive.nii.gz"
    export_path = label_dir / "seg-uid.nii.gz"
    sitk.WriteImage(_mask_on_grid(size, point, 2), str(archive_path))
    # The cached export simulates a formerly repaired mask with the right
    # header but the wrong Z voxel order. It must never drive a reused pair.
    sitk.WriteImage(
        _mask_on_grid(size, (1, 0, 3) if reused else point),
        str(export_path),
    )
    monkeypatch.setattr(utils, "MEDICAL_LABELS_DIR", label_dir)

    archive_reads = []
    class ArchiveStore:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def ensure_local(self, session_id, ref):
            archive_reads.append((session_id, ref.seg_series_uid))
            return archive_path

    monkeypatch.setattr(utils, "MaskStore", ArchiveStore)
    metadata = pydicom.Dataset()
    metadata.ManufacturerModelName = "Interactive Review"
    metadata.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    monkeypatch.setattr(utils.pydicom, "dcmread", lambda *_args, **_kwargs: metadata)

    def respond(request):
        path = request.url.path
        if path == "/tools/find":
            return httpx.Response(200, json=["series-id"])
        if path == "/series/series-id":
            return httpx.Response(200, json={"Instances": ["instance-id"]})
        if path == "/instances/instance-id/file":
            return httpx.Response(200, content=b"metadata")
        raise AssertionError(path)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        successful, skipped = await utils.download_and_pair_volumes(
            str(tmp_path),
            [{
                "pair_id": "volume-uid__target",
                "artifact_key": "pair-mask",
                "volume_uid": "volume-uid",
                "target_id": "target",
                "label_uid": "seg-uid",
                "reused": reused,
            }],
            download_volumes=False,
            client=client,
            session_id="synthetic-session",
        )

    assert len(successful) == 1
    assert skipped == []
    aligned = sitk.GetArrayFromImage(
        sitk.ReadImage(str(workspace_subpath(tmp_path, "masks/pair-mask.nii.gz")))
    )
    assert int(aligned.sum()) == 1
    assert int(aligned[point[2], point[1], point[0]]) == 1
    assert archive_reads == ([("synthetic-session", "seg-uid")] if reused else [])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_archive_read_never_reuses_a_stale_monai_mask(
    tmp_path, monkeypatch
):
    volume_dir = workspace_subpath(tmp_path, "volumes")
    volume_dir.mkdir(parents=True)
    sitk.WriteImage(
        sitk.Image((4, 3, 5), sitk.sitkInt16),
        str(volume_dir / "volume-uid.nii.gz"),
    )
    label_dir = tmp_path / "monai-label-cache"
    label_dir.mkdir()
    sitk.WriteImage(
        _mask_on_grid((4, 3, 5), (1, 0, 3)),
        str(label_dir / "seg-uid.nii.gz"),
    )
    monkeypatch.setattr(utils, "MEDICAL_LABELS_DIR", label_dir)

    class FailingArchive:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def ensure_local(self, _session_id, _ref):
            raise RuntimeError("synthetic archive failure")

    monkeypatch.setattr(utils, "MaskStore", FailingArchive)

    successful, skipped = await utils.download_and_pair_volumes(
        str(tmp_path),
        [{
            "pair_id": "volume-uid__target",
            "artifact_key": "pair-mask",
            "volume_uid": "volume-uid",
            "target_id": "target",
            "label_uid": "seg-uid",
            "reused": True,
        }],
        download_volumes=False,
        session_id="synthetic-session",
    )

    assert successful == []
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "mask_export_failed"
    assert not workspace_subpath(tmp_path, "masks/pair-mask.nii.gz").exists()


@pytest.mark.unit
def test_preview_alignment_does_not_rewrite_archived_mask_cache(tmp_path):
    size = (4, 3, 5)
    volume_path = tmp_path / "volume.nii.gz"
    archived_path = tmp_path / "archived.nii.gz"
    sitk.WriteImage(sitk.Image(size, sitk.sitkInt16), str(volume_path))
    sitk.WriteImage(_mask_on_grid(size, (1, 0, 1), 2), str(archived_path))
    original_bytes = archived_path.read_bytes()

    previews = utils.generate_gif_overlays(
        str(tmp_path),
        [{
            "pair_id": "volume-uid__target",
            "artifact_key": "pair-mask",
            "volume_uid": "volume-uid",
            "target_id": "target",
            "volume_path": str(volume_path),
            "mask_path": str(archived_path),
        }],
    )

    assert len(previews) == 1
    assert previews[0]["isEmptyMask"] is False
    assert archived_path.read_bytes() == original_bytes
