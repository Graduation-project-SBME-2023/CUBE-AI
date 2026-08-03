"""Pure helpers for building cohort-complete multi-organ radiomics tables."""

from __future__ import annotations

import re
from typing import Any, Iterable

import pandas as pd


def normalize_organ_name(value: Any) -> str:
    """Return a stable feature namespace that never contains the reserved delimiter."""
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value).strip().casefold()).strip("_")
    if not normalized:
        raise ValueError(f"Invalid empty organ/target name: {value!r}")
    return normalized


def build_fusion_table(
    cohort: pd.DataFrame,
    extracted_by_subject: dict[str, dict[str, Any]],
    organs: Iterable[str],
    id_column: str,
    target_column: str,
) -> pd.DataFrame:
    """Outer-join extracted organ features onto every labeled cohort subject."""
    if id_column not in cohort.columns or target_column not in cohort.columns:
        raise ValueError("Cohort is missing its ID or target column")
    if cohort[id_column].duplicated().any():
        duplicates = cohort.loc[cohort[id_column].duplicated(), id_column].astype(str).tolist()
        raise ValueError(f"Cohort IDs must be unique; duplicates: {duplicates[:10]}")

    normalized_organs = sorted({normalize_organ_name(organ) for organ in organs})
    feature_columns = sorted(
        {
            column
            for values in extracted_by_subject.values()
            for column in values
            if "__" in column
        }
    )
    rows = []
    for _, cohort_row in cohort.iterrows():
        subject_id = str(cohort_row[id_column])
        row = {
            "subject_id": subject_id,
            target_column: cohort_row[target_column],
            **extracted_by_subject.get(subject_id, {}),
        }
        for organ in normalized_organs:
            organ_features = [
                column for column in feature_columns if column.startswith(f"{organ}__")
            ]
            row[f"{organ}_present"] = int(
                bool(organ_features)
                and any(pd.notna(row.get(column)) for column in organ_features)
            )
        rows.append(row)

    output = pd.DataFrame(rows)
    ordered = ["subject_id", target_column]
    ordered.extend(f"{organ}_present" for organ in normalized_organs)
    ordered.extend(feature_columns)
    for column in ordered:
        if column not in output.columns:
            output[column] = pd.NA
    return output[ordered]


def build_availability_audit(fusion_table: pd.DataFrame, organs: Iterable[str]) -> pd.DataFrame:
    rows = []
    for _, subject in fusion_table.iterrows():
        for organ in sorted({normalize_organ_name(value) for value in organs}):
            present = int(subject.get(f"{organ}_present", 0) or 0)
            rows.append(
                {
                    "subject_id": subject["subject_id"],
                    "organ": organ,
                    "present": present,
                    "status": "extracted" if present else "missing_or_failed",
                }
            )
    return pd.DataFrame(rows)
