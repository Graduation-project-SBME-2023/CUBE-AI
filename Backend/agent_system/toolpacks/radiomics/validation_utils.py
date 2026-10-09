"""Pre-extraction checks of one mask.

- ``check_mask_voxels``: a mask under ``MIN_VOXEL_COUNT`` voxels gives NaN-only
  features or crashes PyRadiomics, so it is not measured;
- ``check_spatial_alignment``: a volume and mask whose affines differ are
  measured anyway, with a warning.

Both are defensive: a missing nibabel or an unreadable header never blocks
an extraction.
"""

import logging
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Masks below this threshold produce NaN-only features or crash the extractor.
MIN_VOXEL_COUNT: int = 10


def check_mask_voxels(binary_array: np.ndarray) -> Tuple[bool, int]:
    """
    Verify that a binary mask contains enough voxels to produce meaningful features.

    Args:
        binary_array: A 2D or 3D NumPy array with values 0/1.

    Returns:
        (is_valid, voxel_count)
            is_valid  — False when voxel_count < MIN_VOXEL_COUNT.
            voxel_count — raw integer count of foreground voxels.
    """
    voxel_count = int(np.sum(binary_array))
    return voxel_count >= MIN_VOXEL_COUNT, voxel_count


def check_spatial_alignment(
    image_path: str,
    mask_path: str,
    atol: float = 1e-3,
) -> Tuple[bool, Optional[str]]:
    """
    Compare the affine transformation matrix (and implied voxel spacing) of
    a NIfTI image against its segmentation mask using nibabel.

    Args:
        image_path: Absolute path to the NIfTI volume.
        mask_path:  Absolute path to the NIfTI mask.
        atol:       Absolute tolerance for affine comparison (default 1 mm).

    Returns:
        (is_aligned, warning_message_or_None)
            is_aligned       — True when affines match within atol.
            warning_message  — Human-readable description of the mismatch, or
                               None when aligned / nibabel unavailable.

    Safety:
        - Returns (True, None) if nibabel is not installed.
        - Returns (True, None) on any unexpected exception.
        Extraction always proceeds; callers should only log the warning.
    """
    try:
        import nibabel as nib

        vol_img  = nib.load(image_path)
        mask_img = nib.load(mask_path)

        vol_affine  = vol_img.affine
        mask_affine = mask_img.affine

        if not np.allclose(vol_affine, mask_affine, atol=atol):
            # Derive voxel spacings from the affine columns for a human-readable message
            def _spacing(aff: np.ndarray) -> Tuple[float, ...]:
                return tuple(np.sqrt((aff[:3, :3] ** 2).sum(axis=0)).round(3))

            vol_spacing  = _spacing(vol_affine)
            mask_spacing = _spacing(mask_affine)
            msg = (
                f"Affine mismatch detected — "
                f"volume spacing={vol_spacing}, mask spacing={mask_spacing}. "
                f"Extraction proceeds but feature values may be unreliable. "
                f"Consider resampling masks to match volume space."
            )
            return False, msg

    except ImportError:
        # nibabel not installed — skip silently, never block extraction
        pass
    except Exception as exc:
        logger.debug(f"[spatial_check] Unexpected error for {image_path}: {exc}")

    return True, None
