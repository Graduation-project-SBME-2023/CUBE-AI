"""Read an ingested series back out of Orthanc and prove it matches the source.

A conversion bug does not raise -- it produces a plausible-looking series that is
mirrored, shifted, or rescaled wrongly, and the error only surfaces much later as
odd radiomics numbers.  The only reliable defence is to fetch what was actually
archived and compare it against the file that was uploaded.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import SimpleITK as sitk

from clients.orthanc_client import OrthancClient
from imaging.geometry import (
    describe_geometry,
    geometry_from_dicom_series,
    geometry_from_sitk,
    sort_dicom_series,
)
from ingestion.nifti_to_dicom import datasets_to_array, read_volume

logger = logging.getLogger(__name__)

SPACING_ATOL = 1e-3
ORIGIN_ATOL = 1e-2
DIRECTION_ATOL = 1e-4


async def verify_round_trip(
    orthanc: OrthancClient,
    *,
    series_instance_uid: str,
    source_path: str | Path,
    rescale_slope: float,
) -> dict[str, Any]:
    """Compare the archived series against the source volume.

    Returns a report rather than raising, so the caller decides whether a
    mismatch should fail the job or only warn.
    """
    source_image = read_volume(source_path)
    source_geometry = geometry_from_sitk(source_image)
    source_array = sitk.GetArrayFromImage(source_image).astype(np.float64)

    datasets = sort_dicom_series(
        await orthanc.read_series_datasets(series_instance_uid)
    )
    stored_geometry = geometry_from_dicom_series(datasets)
    stored_array = datasets_to_array(datasets)

    size_ok = tuple(source_geometry.size_xyz) == tuple(stored_geometry.size_xyz)
    spacing_ok = bool(
        np.allclose(
            source_geometry.spacing_xyz, stored_geometry.spacing_xyz, atol=SPACING_ATOL
        )
    )
    origin_ok = bool(
        np.allclose(
            source_geometry.origin_lps, stored_geometry.origin_lps, atol=ORIGIN_ATOL
        )
    )
    direction_ok = bool(
        np.allclose(
            np.asarray(source_geometry.direction, dtype=float),
            np.asarray(stored_geometry.direction, dtype=float),
            atol=DIRECTION_ATOL,
        )
    )

    # Half a quantisation step is the largest error a correct linear rescale can
    # introduce; an identity rescale must reproduce the values exactly.
    voxel_tolerance = float(abs(rescale_slope)) / 2.0 + 1e-6
    if size_ok:
        max_difference = float(np.max(np.abs(stored_array - source_array)))
        voxels_ok = max_difference <= voxel_tolerance
    else:
        max_difference = float("inf")
        voxels_ok = False

    report: dict[str, Any] = {
        "size_ok": size_ok,
        "spacing_ok": spacing_ok,
        "origin_ok": origin_ok,
        "direction_ok": direction_ok,
        "voxels_ok": voxels_ok,
        "max_voxel_difference": max_difference,
        "voxel_tolerance": voxel_tolerance,
        "slice_count": len(datasets),
        "source_geometry": describe_geometry(source_geometry),
        "stored_geometry": describe_geometry(stored_geometry),
    }
    report["passed"] = all(
        (size_ok, spacing_ok, origin_ok, direction_ok, voxels_ok)
    )
    report["failures"] = [
        name
        for name in ("size", "spacing", "origin", "direction", "voxels")
        if not report[f"{name}_ok"]
    ]

    if report["passed"]:
        logger.info(
            "Round-trip verification passed for %s (%s slices, max voxel delta %.6g)",
            series_instance_uid,
            len(datasets),
            max_difference,
        )
    else:
        logger.error(
            "Round-trip verification failed for %s: %s",
            series_instance_uid,
            ", ".join(report["failures"]),
        )
    return report
