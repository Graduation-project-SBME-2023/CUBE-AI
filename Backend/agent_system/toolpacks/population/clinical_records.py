"""The clinical records of a population: where they are read from, and how a
record is matched to a cohort member by patient identity. Pure functions
except ``load_clinical_records``, which reads files."""

from __future__ import annotations

import csv
import json
import os
from collections.abc import Iterable
from typing import Any

from storage_paths import workspace_subpath

# The bundled sample dataset, used when the workspace has no clinical file.
SAMPLE_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_ID_FIELDS = ("case_id", "patient_id")
_ID_PREFIXES = ("kits-", "kits23-", "kits23_", "case-", "case_")
MAX_RECORD_BYTES = 200 * 1024

type Record = tuple[str, dict[str, Any]]  # (patient id, record)


def normalize_patient_ids(patient_id: str | None) -> set[str]:
    """The spellings one patient id may take across datasets (``KiTS-001``,
    ``case_001``, ``001``, ...)."""
    if not patient_id:
        return set()
    cleaned = str(patient_id).strip()
    lower = cleaned.lower()
    spellings = {cleaned, lower}
    for prefix in _ID_PREFIXES:
        if lower.startswith(prefix):
            suffix = lower[len(prefix):]
            spellings |= {f"case_{suffix}", f"KiTS_{suffix}", f"KiTS-{suffix}", f"KITS-{suffix}", suffix}
    if cleaned.isdigit():
        spellings |= {f"case_{cleaned}", f"KiTS-{cleaned}", f"KITS-{cleaned}"}
    return spellings


def _record_id(record: dict[str, Any]) -> str | None:
    return next((str(record[name]) for name in _ID_FIELDS if record.get(name)), None)


def _read_json(path: str) -> list[Record]:
    with open(path, "r", encoding="utf-8") as stream:
        data = json.load(stream)
    if isinstance(data, list):
        return [(patient_id, item) for item in data if (patient_id := _record_id(item))]
    if isinstance(data, dict):
        return [
            (str(key), value if isinstance(value, dict) else {"report": value})
            for key, value in data.items()
            if key
        ]
    return []


def _read_csv(path: str) -> list[Record]:
    with open(path, "r", encoding="utf-8") as stream:
        return [(patient_id, dict(row)) for row in csv.DictReader(stream) if (patient_id := _record_id(row))]


def load_clinical_records(workspace_dir: str | None) -> tuple[list[Record], str | None]:
    """The records of the first clinical source that exists: the workspace's
    ``clinical_data.json`` / ``.csv``, else the bundled sample. Returns the
    records and the path read (``None`` when there is no source)."""
    candidates: list[str] = []
    if workspace_dir:
        candidates += [
            str(workspace_subpath(workspace_dir, "clinical_data.json")),
            str(workspace_subpath(workspace_dir, "clinical_data.csv")),
        ]
    candidates += [
        os.path.join(SAMPLE_DATA_DIR, "kits23_reduced.json"),
        os.path.join(SAMPLE_DATA_DIR, "kits23_reduced.csv"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return (_read_json(path) if path.endswith(".json") else _read_csv(path)), path
    return [], None


def match_records(
    records: Iterable[Record], member_ids: dict[str, set[str]]
) -> dict[str, Record]:
    """Each member's record (by volume uid), matched by patient identity only;
    the first matching record wins."""
    matches: dict[str, Record] = {}
    for patient_id, record in records:
        keys = normalize_patient_ids(patient_id) | {patient_id}
        for uid, identifiers in member_ids.items():
            if uid not in matches and keys & identifiers:
                matches[uid] = (patient_id, record)
    return matches


def flatten(record: dict[str, Any]) -> str:
    """The record as one line of ``key: value`` pairs, without its id fields."""
    return " | ".join(f"{key}: {value}" for key, value in record.items() if key not in _ID_FIELDS)


def bounded_record(record: dict[str, Any]) -> dict[str, Any]:
    """The per-member clinical evidence, bounded to ``MAX_RECORD_BYTES``."""
    text = json.dumps(record, ensure_ascii=False, default=str)
    if len(text.encode("utf-8")) > MAX_RECORD_BYTES:
        return {"schema_version": 1, "truncated": True, "text": text[: MAX_RECORD_BYTES // 2]}
    return {"schema_version": 1, "record": json.loads(text)}


__all__ = [
    "Record",
    "bounded_record",
    "flatten",
    "load_clinical_records",
    "match_records",
    "normalize_patient_ids",
]
