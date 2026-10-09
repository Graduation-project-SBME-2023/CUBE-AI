"""What an extraction reports: the per-organ audit of the recorded rows,
built from the rows exactly as feature selection will read them."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..feature_set_builder import SUBJECT_KEY, WideTable

MAX_FAILED_IDS = 50


@dataclass(frozen=True)
class Audit:
    records: list[dict[str, Any]]  # one per subject and organ

    @property
    def missing(self) -> list[dict[str, Any]]:
        return [item for item in self.records if not item["present"]]


def audit(table: WideTable, attempted: set[tuple[str, str]], warnings: Mapping[str, str]) -> Audit:
    """Each subject's organs: extracted, attempted without a result, or never segmented."""
    records: list[dict[str, Any]] = []
    for record in table.frame.to_dict("records"):
        subject = record[SUBJECT_KEY]
        for organ in table.organs:
            present = int(record.get(f"{organ}_present", 0))
            if present:
                status = "extracted"
            elif (subject, organ) in attempted:
                status = "extraction_failed_or_empty"
            else:
                status = "segmentation_missing"
            records.append({
                "subject_key": subject,
                "organ": organ,
                "present": present,
                "status": status,
                "spatial_warning": warnings.get(subject, ""),
            })
    return Audit(records)


def features_per_organ(table: WideTable) -> dict[str, int]:
    return {
        organ: sum(
            1 for column in table.feature_columns
            if column.startswith(f"{organ}__") or column.startswith(f"{organ}_label_")
        )
        for organ in table.organs
    }


def volume_range(table: WideTable) -> list[float]:
    """The smallest and largest measured lesion volume, or [] when none was measured."""
    columns = [column for column in table.feature_columns if "voxelvolume" in column.lower()]
    if not columns:
        return []
    volumes = pd.to_numeric(table.frame[columns[0]], errors="coerce").dropna()
    return [] if volumes.empty else [round(float(volumes.min()), 2), round(float(volumes.max()), 2)]


def subjects_with_features(table: WideTable) -> int:
    presence = [f"{organ}_present" for organ in table.organs]
    return int(sum(1 for record in table.frame.to_dict("records") if any(record.get(name) for name in presence)))


def summary(
    table: WideTable,
    report: Audit,
    *,
    warnings: Mapping[str, str],
    params_used: dict[str, Any],
    gpu_available: bool,
) -> dict[str, Any]:
    missing = report.missing
    return {
        "total_patients": int(len(table.frame)),
        "successful_extractions": subjects_with_features(table),
        "failed_count": len(missing),
        "failed_patient_ids": [item["subject_key"] for item in missing][:MAX_FAILED_IDS],
        "patients_with_spatial_warnings": list(warnings),
        "volume_range": volume_range(table),
        "extraction_params_used": params_used,
        "gpu_available": gpu_available,
    }


__all__ = ["Audit", "audit", "features_per_organ", "subjects_with_features", "summary", "volume_range"]
