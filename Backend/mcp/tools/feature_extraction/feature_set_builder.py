"""Feature-set assembly helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

SUBJECT_KEY = "subject_key"
TARGET_COLUMN = "target"
FEATURE_SET_SCHEMA_VERSION = 1
# Identity and label columns can never be features (rule 3).
FORBIDDEN_FEATURE_NAMES = frozenset({"target", "label", "subject_key", "ordinal"})
# Exactly the manifest feature_selection_split.json held.
SELECTION_FIELDS = (
    "strategy",
    "group_column",
    "n_groups",
    "holdout_size",
    "seed",
    "holdout_groups",
    "train_row_count",
    "holdout_row_count",
    "label_supervised_on_full_data",
    "degraded_reason",
)
MISSING_VALUE_REASON = (
    "no finite value was measured for this subject: the extractor returned a "
    "non-finite value, or the subject had no mask for that target"
)
# Columns that can identify a row of a generic CSV, most specific first.
_CSV_ID_COLUMNS = (
    "subject_key",
    "volume_series_instance_uid",
    "subject_id",
    "id",
    "series_uid",
    "patient_id",
)


class FeatureSetBuildError(ValueError):
    """The inputs cannot form an honest feature set."""


@dataclass
class WideTable:
    """Features by subject, labelled, in cohort order."""

    frame: pd.DataFrame
    feature_columns: list[str]
    source_feature_ids: list[str] = field(default_factory=list)
    source_digests: list[str] = field(default_factory=list)
    organs: list[str] = field(default_factory=list)


def finite_or_none(value: Any) -> float | None:
    """A matrix cell: a finite float, or None for a recorded missing value."""
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        value = value.item()
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FeatureSetBuildError(f"feature value {value!r} is not numeric") from exc
    return number if math.isfinite(number) else None


def _subject_targets(analysis_rows: Sequence[Mapping[str, Any]]) -> tuple[dict, dict]:
    """subject_key -> target and subject_key -> first ordinal."""
    targets: dict[str, Any] = {}
    first_ordinal: dict[str, int] = {}
    for row in analysis_rows:
        key = str(row["subject_key"])
        if key in targets and targets[key] != row["target"]:
            raise FeatureSetBuildError(
                f"subject {key!r} has members labelled both {targets[key]!r} and "
                f"{row['target']!r}; correct the cohort labels before selecting features"
            )
        targets[key] = row["target"]
        ordinal = int(row["ordinal"])
        first_ordinal[key] = min(first_ordinal.get(key, ordinal), ordinal)
    return targets, first_ordinal


def _ordered_subjects(first_ordinal: Mapping[str, int]) -> list[str]:
    return sorted(first_ordinal, key=lambda key: (first_ordinal[key], key))


def wide_table_from_rows(
    radiomics_rows: Sequence[Mapping[str, Any]],
    analysis_rows: Sequence[Mapping[str, Any]],
) -> WideTable:
    """The fused, labelled table from `radiomics_features` rows."""
    if not analysis_rows:
        raise FeatureSetBuildError("the cohort has no labelled member")
    members = {str(row["volume_series_instance_uid"]): row for row in analysis_rows}
    targets, first_ordinal = _subject_targets(analysis_rows)

    used = [row for row in radiomics_rows if str(row["source_volume_uid"]) in members]
    if not used:
        raise FeatureSetBuildError(
            "none of the extracted radiomics rows belongs to a labelled member of the cohort"
        )

    volumes_per_subject: dict[str, set[str]] = {}
    segments_per_target: dict[str, set[int]] = {}
    for row in used:
        subject = str(members[str(row["source_volume_uid"])]["subject_key"])
        volumes_per_subject.setdefault(subject, set()).add(str(row["source_volume_uid"]))
        target = str(row.get("target_key") or "roi")
        segments_per_target.setdefault(target, set()).add(int(row["segment_number"]))
    shared = {
        subject: sorted(volumes)
        for subject, volumes in volumes_per_subject.items()
        if len(volumes) > 1
    }
    if shared:
        sample = dict(list(shared.items())[:5])
        raise FeatureSetBuildError(
            "feature_matrix is keyed by subject_key (the patient-level split group), and "
            f"{len(shared)} subject(s) have features from more than one volume: {sample}. "
            "Restrict the cohort to one volume per subject before selecting features."
        )

    per_subject: dict[str, dict[str, float | None]] = {}
    present: dict[str, set[str]] = {}
    source_ids: list[str] = []
    source_digests: list[str] = []
    for row in used:
        subject = str(members[str(row["source_volume_uid"])]["subject_key"])
        target = str(row.get("target_key") or "roi")
        prefix = (
            target
            if len(segments_per_target[target]) == 1
            else f"{target}_label_{int(row['segment_number'])}"
        )
        values = per_subject.setdefault(subject, {})
        for name, value in (row.get("features") or {}).items():
            column = f"{prefix}__{name}"
            if column in values:
                raise FeatureSetBuildError(
                    f"subject {subject!r} has two measurements for {column!r}"
                )
            values[column] = finite_or_none(value)
        present.setdefault(subject, set()).add(target)
        source_ids.append(str(row["id"]))
        if row.get("source_digest"):
            source_digests.append(str(row["source_digest"]))

    organs = sorted(segments_per_target)
    feature_columns = sorted({column for values in per_subject.values() for column in values})
    presence_columns = [f"{organ}_present" for organ in organs]

    records = []
    for subject in _ordered_subjects(first_ordinal):
        values = per_subject.get(subject, {})
        record: dict[str, Any] = {SUBJECT_KEY: subject, TARGET_COLUMN: targets[subject]}
        for column in feature_columns:
            record[column] = values.get(column)
        for organ in organs:
            record[f"{organ}_present"] = int(organ in present.get(subject, set()))
        records.append(record)

    frame = pd.DataFrame.from_records(
        records, columns=[SUBJECT_KEY, TARGET_COLUMN, *feature_columns, *presence_columns]
    )
    return WideTable(
        frame=frame,
        feature_columns=[*feature_columns, *presence_columns],
        source_feature_ids=source_ids,
        source_digests=source_digests,
        organs=organs,
    )


def frame_from_csv(
    frame: pd.DataFrame, analysis_rows: Sequence[Mapping[str, Any]]
) -> tuple[pd.DataFrame, list[str]]:
    """A generic CSV, re-keyed by subject and re-labelled from the cohort."""
    if not analysis_rows:
        raise FeatureSetBuildError("the cohort has no labelled member")
    targets, first_ordinal = _subject_targets(analysis_rows)
    by_volume = {
        str(row["volume_series_instance_uid"]): str(row["subject_key"]) for row in analysis_rows
    }
    known_subjects = set(targets)
    notes: list[str] = []

    lowered = {str(column).lower(): column for column in frame.columns}
    id_column = None
    mapped: list[str | None] = []
    for candidate in _CSV_ID_COLUMNS:
        column = lowered.get(candidate)
        if column is None:
            continue
        values = [str(value).strip() for value in frame[column].tolist()]
        attempt = [
            value if value in known_subjects else by_volume.get(value)
            for value in values
        ]
        if any(item is not None for item in attempt):
            id_column, mapped = column, attempt
            break
    if id_column is None:
        raise FeatureSetBuildError(
            "the CSV has no column identifying cohort subjects (one of "
            f"{', '.join(_CSV_ID_COLUMNS)} holding volume UIDs or subject keys)"
        )
    unmatched = [
        str(frame[id_column].iloc[index])
        for index, subject in enumerate(mapped)
        if subject is None
    ]
    if unmatched:
        raise FeatureSetBuildError(
            f"{len(unmatched)} CSV row(s) are not labelled members of the cohort: "
            f"{unmatched[:10]}"
        )
    if len(set(mapped)) != len(mapped):
        raise FeatureSetBuildError(
            "two CSV rows belong to one subject; feature_matrix is keyed by subject_key"
        )

    label_columns = [
        column
        for column in frame.columns
        if str(column).lower() in {"target", "label", "outcome"}
    ]
    if label_columns:
        notes.append(
            f"dropped label column(s) {label_columns}: the cohort member's label is used"
        )
    features = frame.drop(columns=[id_column, *label_columns])
    features.insert(0, TARGET_COLUMN, [targets[subject] for subject in mapped])
    features.insert(0, SUBJECT_KEY, mapped)
    features["__ordinal"] = [first_ordinal[subject] for subject in mapped]
    features = features.sort_values(["__ordinal", SUBJECT_KEY]).drop(columns="__ordinal")
    return features.reset_index(drop=True), notes


def check_feature_names(names: Sequence[str]) -> list[str]:
    cleaned = [str(name) for name in names]
    if not cleaned:
        raise FeatureSetBuildError("a feature set needs at least one feature")
    if any(not name.strip() for name in cleaned):
        raise FeatureSetBuildError("feature names must not be blank")
    if len(set(cleaned)) != len(cleaned):
        raise FeatureSetBuildError("feature names must be unique")
    forbidden = [name for name in cleaned if name.strip().lower() in FORBIDDEN_FEATURE_NAMES]
    if forbidden:
        raise FeatureSetBuildError(
            f"{forbidden} cannot be a feature: identity and label columns never enter "
            "feature_matrix (the label is joined from the cohort at read time)"
        )
    return cleaned


def matrix_from_frame(
    frame: pd.DataFrame, feature_names: Sequence[str]
) -> tuple[dict[str, list[float | None]], int]:
    """`{subject_key: [value, …]}` in `feature_names` order, and the number of
    missing cells (recorded in `quality_summary`, never hidden)."""
    names = check_feature_names(feature_names)
    missing_columns = [name for name in names if name not in frame.columns]
    if missing_columns:
        raise FeatureSetBuildError(f"feature(s) {missing_columns[:10]} are not in the table")
    matrix: dict[str, list[float | None]] = {}
    missing = 0
    for record in frame[[SUBJECT_KEY, *names]].itertuples(index=False, name=None):
        subject = str(record[0])
        if subject in matrix:
            raise FeatureSetBuildError(f"subject {subject!r} appears twice in the table")
        values = [finite_or_none(value) for value in record[1:]]
        missing += sum(value is None for value in values)
        matrix[subject] = values
    if not matrix:
        raise FeatureSetBuildError("the feature matrix has no row")
    return matrix, missing


def assemble_rows(
    feature_names: Sequence[str],
    feature_matrix: Mapping[str, Sequence[float | None]],
    subjects_in_ordinal_order: Iterable[str],
) -> list[list[float | None]]:
    """The reader's side of rule 2: rows in cohort order, columns in name order."""
    width = len(feature_names)
    rows = []
    for subject in subjects_in_ordinal_order:
        if subject not in feature_matrix:
            continue
        values = list(feature_matrix[subject])
        if len(values) != width:
            raise FeatureSetBuildError(f"row {subject!r} is not rectangular")
        rows.append(values)
    return rows


def selection_document(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """`feature_sets.selection` from a `SelectionSplit.to_manifest`."""
    document: dict[str, Any] = {"schema_version": FEATURE_SET_SCHEMA_VERSION}
    for name in SELECTION_FIELDS:
        document[name] = manifest.get(name)
    document["holdout_groups"] = sorted(str(group) for group in (document["holdout_groups"] or []))
    document["label_supervised_on_full_data"] = bool(document["label_supervised_on_full_data"])
    for name in ("n_groups", "seed", "train_row_count", "holdout_row_count"):
        if document[name] is not None:
            document[name] = int(document[name])
    if document["holdout_size"] is not None:
        document["holdout_size"] = float(document["holdout_size"])
    return document


def quality_summary(
    *, subjects: int, feature_count: int, missing_values: int, notes: Sequence[str] = ()
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "schema_version": FEATURE_SET_SCHEMA_VERSION,
        "subjects": int(subjects),
        "features": int(feature_count),
        "missing_values": {"count": int(missing_values)},
    }
    if missing_values:
        # A value that is not finite is recorded with an explicit reason.
        summary["missing_values"]["reason"] = MISSING_VALUE_REASON
    if notes:
        summary["notes"] = [str(note) for note in notes]
    return summary


__all__ = [
    "FEATURE_SET_SCHEMA_VERSION",
    "FORBIDDEN_FEATURE_NAMES",
    "FeatureSetBuildError",
    "MISSING_VALUE_REASON",
    "SELECTION_FIELDS",
    "SUBJECT_KEY",
    "TARGET_COLUMN",
    "WideTable",
    "assemble_rows",
    "check_feature_names",
    "finite_or_none",
    "frame_from_csv",
    "matrix_from_frame",
    "quality_summary",
    "selection_document",
    "wide_table_from_rows",
]
