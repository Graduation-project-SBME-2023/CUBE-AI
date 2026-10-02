from __future__ import annotations

import numpy as np
import pytest

from agent_system.toolpacks.radiomics import validation_utils as module


@pytest.mark.unit
@pytest.mark.parametrize(
    ("voxel_count", "expected_valid"),
    [(0, False), (9, False), (10, True), (25, True)],
)
def test_mask_voxel_threshold(voxel_count, expected_valid):
    mask = np.zeros(30, dtype=np.uint8)
    mask[:voxel_count] = 1

    is_valid, observed_count = module.check_mask_voxels(mask)

    assert observed_count == voxel_count
    assert is_valid is expected_valid


@pytest.mark.unit
def test_spatial_alignment_accepts_matching_affines(tmp_path):
    nib = pytest.importorskip("nibabel")
    image_path = tmp_path / "image.nii.gz"
    mask_path = tmp_path / "mask.nii.gz"
    affine = np.diag([1.0, 1.0, 2.0, 1.0])
    nib.save(nib.Nifti1Image(np.zeros((3, 3, 3)), affine), image_path)
    nib.save(nib.Nifti1Image(np.ones((3, 3, 3)), affine), mask_path)

    assert module.check_spatial_alignment(
        str(image_path),
        str(mask_path),
    ) == (True, None)


@pytest.mark.unit
def test_spatial_alignment_reports_spacing_mismatch(tmp_path):
    nib = pytest.importorskip("nibabel")
    image_path = tmp_path / "image.nii.gz"
    mask_path = tmp_path / "mask.nii.gz"
    nib.save(
        nib.Nifti1Image(np.zeros((3, 3, 3)), np.diag([1, 1, 1, 1])),
        image_path,
    )
    nib.save(
        nib.Nifti1Image(np.ones((3, 3, 3)), np.diag([2, 2, 2, 1])),
        mask_path,
    )

    is_aligned, warning = module.check_spatial_alignment(
        str(image_path),
        str(mask_path),
    )

    assert not is_aligned
    assert "Affine mismatch" in warning
    assert "volume spacing" in warning

