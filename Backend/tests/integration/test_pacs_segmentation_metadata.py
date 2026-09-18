from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx
import pytest


RUN_PACS_AUDIT = os.environ.get("RUN_PACS_METADATA_AUDIT") == "1"
CONTROL_BASE_URL = os.environ.get("CONTROL_BASE_URL", "http://localhost:8001")
PAGE_SIZE = 100

_STANDARD_SERIES_DESCRIPTION = re.compile(
    r"^\s*\d{2}/\d{2}/\d{4}\s+-\s+.+?\s+-\s+(.+?)\s*$",
    re.IGNORECASE,
)


def _normalize_label(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").split()).casefold()


def _description_target(description: Any) -> str | None:
    desc = str(description or "").strip()
    match = _STANDARD_SERIES_DESCRIPTION.match(desc)
    if match:
        return match.group(1).strip()

    match = re.match(r"^\s*Segmentation:\s*(.+?)\s*$", desc, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    match = re.match(r"^\s*(.+?)-.+?-\d{2}:\d{2}:\d{2}\s+[AP]M-g\s*$", desc, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    return None


def _metadata_conflict(segmentation: dict[str, Any]) -> dict[str, str] | None:
    segment_label = str(segmentation.get("target_label") or "").strip()
    description_target = _description_target(segmentation.get("series_description"))
    if not segment_label or not description_target:
        return None
    if _normalize_label(segment_label) == _normalize_label(description_target):
        return None

    return {
        "volume_uid": str(segmentation.get("volume_series_instance_uid") or ""),
        "segmentation_uid": str(
            segmentation.get("label_series_instance_uid") or ""
        ),
        "series_description": str(segmentation.get("series_description") or ""),
        "segment_label": segment_label,
        "description_target": description_target,
    }


def _fetch_all_segmentations(client: httpx.Client) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    skip = 0
    while True:
        response = client.get(
            f"{CONTROL_BASE_URL.rstrip('/')}/segmentations/",
            params={"skip": skip, "limit": PAGE_SIZE},
        )
        response.raise_for_status()
        page = response.json()
        assert isinstance(page, list), "Segmentations endpoint must return a list"
        records.extend(page)
        if len(page) < PAGE_SIZE:
            return records
        skip += PAGE_SIZE


def test_metadata_conflict_detects_kidney_mask_labeled_as_spleen():
    conflict = _metadata_conflict(
        {
            "volume_series_instance_uid": "volume-uid",
            "label_series_instance_uid": "segmentation-uid",
            "series_description": "08/08/2026 - voxtell - kidney",
            "target_label": "spleen",
        }
    )

    assert conflict == {
        "volume_uid": "volume-uid",
        "segmentation_uid": "segmentation-uid",
        "series_description": "08/08/2026 - voxtell - kidney",
        "segment_label": "spleen",
        "description_target": "kidney",
    }


def test_metadata_conflict_accepts_matching_labels():
    assert (
        _metadata_conflict(
            {
                "series_description": "12/08/2026 - voxtell - left kidney",
                "target_label": "left_kidney",
            }
        )
        is None
    )


def test_metadata_conflict_detects_new_formats():
    assert (
        _metadata_conflict(
            {
                "volume_series_instance_uid": "volume-uid",
                "label_series_instance_uid": "segmentation-uid",
                "series_description": "kidney-voxtell-08:45:30 PM-g",
                "target_label": "spleen",
            }
        )["description_target"]
        == "kidney"
    )
    assert (
        _metadata_conflict(
            {
                "volume_series_instance_uid": "volume-uid",
                "label_series_instance_uid": "segmentation-uid",
                "series_description": "Segmentation: kidney",
                "target_label": "spleen",
            }
        )["description_target"]
        == "kidney"
    )


@pytest.mark.integration
@pytest.mark.docker
@pytest.mark.skipif(
    not RUN_PACS_AUDIT,
    reason="Set RUN_PACS_METADATA_AUDIT=1 to audit every deployed PACS mask.",
)
def test_every_volume_mask_has_consistent_target_metadata():
    """Audit every paginated segmentation record without modifying PACS."""
    with httpx.Client(timeout=30.0) as client:
        segmentations = _fetch_all_segmentations(client)

    conflicts = [
        conflict
        for segmentation in segmentations
        if (conflict := _metadata_conflict(segmentation)) is not None
    ]

    assert not conflicts, (
        f"Found {len(conflicts)} inconsistent masks among "
        f"{len(segmentations)} PACS segmentation records:\n"
        f"{json.dumps(conflicts, indent=2)}"
    )
