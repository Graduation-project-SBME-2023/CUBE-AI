"""Resolve outcome columns without relying on one hardcoded column name."""

from __future__ import annotations

import json
import os
from typing import Any, Iterable, Optional


_TARGET_ALIASES = ("target", "y", "label", "outcome", "response", "endpoint")
_IDENTIFIER_NAMES = {
    "id",
    "subject_id",
    "subjectid",
    "patient_id",
    "patientid",
    "case_id",
    "caseid",
    "name",
    "uid",
    "volume_uid",
    "pat_id",
    "unnamed: 0",
}


def load_feature_schema(input_path: str) -> dict[str, Any]:
    """Load feature_schema.json next to a radiomics CSV when it exists."""
    schema_path = os.path.join(os.path.dirname(input_path), "feature_schema.json")
    if not os.path.isfile(schema_path):
        return {}
    try:
        with open(schema_path, "r", encoding="utf-8") as stream:
            schema = json.load(stream)
        return schema if isinstance(schema, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def resolve_target_column(
    columns: Iterable[str],
    requested: Optional[str] = None,
    schema: Optional[dict[str, Any]] = None,
    feature_delimiter: str = "__",
) -> tuple[str, str]:
    """Return the target column and the evidence used to select it."""
    available = [str(column) for column in columns]
    by_casefold: dict[str, list[str]] = {}
    for column in available:
        by_casefold.setdefault(column.casefold(), []).append(column)

    def unique_casefold(name: Any) -> Optional[str]:
        if name is None:
            return None
        matches = by_casefold.get(str(name).strip().casefold(), [])
        return matches[0] if len(matches) == 1 else None

    requested = requested.strip() if requested else None
    exact_requested = unique_casefold(requested)
    if exact_requested:
        method = "exact" if exact_requested == requested else "case_insensitive"
        return exact_requested, method

    schema = schema or {}
    schema_target = unique_casefold(schema.get("target_column"))
    schema_source = schema.get("source_target_column")
    if requested and schema_target and schema_source:
        if requested.casefold() == str(schema_source).strip().casefold():
            return schema_target, "schema_source_mapping"
    if schema_target:
        return schema_target, "feature_schema"

    for alias in _TARGET_ALIASES:
        match = unique_casefold(alias)
        if match:
            return match, "standard_alias"

    candidates = []
    for column in available:
        normalized = column.strip().casefold()
        if feature_delimiter in column or normalized.endswith("_present"):
            continue
        if normalized in _IDENTIFIER_NAMES or normalized.endswith("_id"):
            continue
        candidates.append(column)

    if len(candidates) == 1:
        return candidates[0], "unique_non_feature_column"

    requested_note = f" Requested target: {requested!r}." if requested else ""
    if candidates:
        raise ValueError(
            "Target column is ambiguous. "
            f"Candidate outcome columns: {candidates}.{requested_note} "
            "Specify target_column or provide feature_schema.json."
        )
    raise ValueError(
        "No target column could be resolved. "
        f"Available columns: {available}.{requested_note}"
    )
