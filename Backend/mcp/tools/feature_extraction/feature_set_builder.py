"""Feature-set assembly helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

VOLUME_KEY = "volume_series_instance_uid"
SUBJECT_KEY = "subject_key"
TARGET_COLUMN = "target"
FEATURE_SET_SCHEMA_VERSION = 1
# Identity and label columns can never be features (rule 3).
FORBIDDEN_FEATURE_NAMES = frozenset(
    {"target", "label", "subject_key", "volume_series_instance_uid", "ordinal"}
)
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
    "no finite value was measured for this volume: the extractor returned a "
    "non-finite value, or the volume had no mask for that target"
)
# Columns that can identify a row of a generic CSV, most specific first.
# A volume-identifying column is unambiguous; a subject-identifying one only
# resolves when that subject has exactly one volume in the cohort.
_CSV_ID_COLUMNS = (
    "volume_series_instance_uid",
    "series_uid",
    "id",
    "subject_key",
    "subject_id",
    "patient_id",
)


class FeatureSetBuildError(ValueError):
    """The inputs cannot form an honest feature set."""


@dataclass
class WideTable:
    """Features by volume, labelled, in cohort order.

    A label is a fact about one volume (``cohort_members.label``), not about a
    patient: one subject may legitimately contribute several volumes with
    different labels (e.g. one lesion per kidney). ``subject_key`` stays on
    the frame as a plain column so the selection split can group a patient's
    volumes together and keep them on one side of the holdout — it is never
    the row identity."""

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


def _volume_targets(analysis_rows: Sequence[Mapping[str, Any]]) -> tuple[dict, dict, dict]:
    """volume -> target, volume -> first ordinal, volume -> subject_key.

    The label is per volume (``cohort_members.label``): each analysis row is
    already unique by ``volume_series_instance_uid``, so unlike a subject-keyed
    lookup this can never see two different targets under one key."""
    targets: dict[str, Any] = {}
    first_ordinal: dict[str, int] = {}
    subjects: dict[str, str] = {}
    for row in analysis_rows:
        key = str(row["volume_series_instance_uid"])
        targets[key] = row["target"]
        subjects[key] = str(row["subject_key"])
        ordinal = int(row["ordinal"])
        first_ordinal[key] = min(first_ordinal.get(key, ordinal), ordinal)
    return targets, first_ordinal, subjects


def _ordered_keys(first_ordinal: Mapping[str, int]) -> list[str]:
    return sorted(first_ordinal, key=lambda key: (first_ordinal[key], key))


def wide_table_from_rows(
    radiomics_rows: Sequence[Mapping[str, Any]],
    analysis_rows: Sequence[Mapping[str, Any]],
) -> WideTable:
    """The fused, labelled table from `radiomics_features` rows."""
    if not analysis_rows:
        raise FeatureSetBuildError("the cohort has no labelled member")
    members = {str(row["volume_series_instance_uid"]): row for row in analysis_rows}
    targets, first_ordinal, subjects = _volume_targets(analysis_rows)

    used = [row for row in radiomics_rows if str(row["source_volume_uid"]) in members]
    if not used:
        raise FeatureSetBuildError(
            "none of the extracted radiomics rows belongs to a labelled member of the cohort"
        )

    segments_per_target: dict[str, set[int]] = {}
    for row in used:
        target = str(row.get("target_key") or "roi")
        segments_per_target.setdefault(target, set()).add(int(row["segment_number"]))

    per_volume: dict[str, dict[str, float | None]] = {}
    present: dict[str, set[str]] = {}
    source_ids: list[str] = []
    source_digests: list[str] = []
    for row in used:
        volume = str(row["source_volume_uid"])
        target = str(row.get("target_key") or "roi")
        prefix = (
            target
            if len(segments_per_target[target]) == 1
            else f"{target}_label_{int(row['segment_number'])}"
        )
        values = per_volume.setdefault(volume, {})
        for name, value in (row.get("features") or {}).items():
            column = f"{prefix}__{name}"
            if column in values:
                raise FeatureSetBuildError(
                    f"volume {volume!r} has two measurements for {column!r}"
                )
            values[column] = finite_or_none(value)
        present.setdefault(volume, set()).add(target)
        source_ids.append(str(row["radiomics_feature_id"]))
        if row.get("source_digest"):
            source_digests.append(str(row["source_digest"]))

    organs = sorted(segments_per_target)
    feature_columns = sorted({column for values in per_volume.values() for column in values})
    presence_columns = [f"{organ}_present" for organ in organs]

    records = []
    for volume in _ordered_keys(first_ordinal):
        values = per_volume.get(volume, {})
        record: dict[str, Any] = {
            VOLUME_KEY: volume,
            SUBJECT_KEY: subjects[volume],
            TARGET_COLUMN: targets[volume],
        }
        for column in feature_columns:
            record[column] = values.get(column)
        for organ in organs:
            record[f"{organ}_present"] = int(organ in present.get(volume, set()))
        records.append(record)

    frame = pd.DataFrame.from_records(
        records,
        columns=[VOLUME_KEY, SUBJECT_KEY, TARGET_COLUMN, *feature_columns, *presence_columns],
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
    """A generic CSV, re-keyed by volume and re-labelled from the cohort."""
    if not analysis_rows:
        raise FeatureSetBuildError("the cohort has no labelled member")
    targets, first_ordinal, subjects = _volume_targets(analysis_rows)
    known_volumes = set(targets)
    subject_volumes: dict[str, list[str]] = {}
    for volume, subject in subjects.items():
        subject_volumes.setdefault(subject, []).append(volume)
    notes: list[str] = []

    def _resolve(value: str) -> str | None:
        if value in known_volumes:
            return value
        # A bare subject key only resolves when it names exactly one volume;
        # a subject with several volumes needs a volume-identifying column.
        candidates = subject_volumes.get(value)
        return candidates[0] if candidates and len(candidates) == 1 else None

    lowered = {str(column).lower(): column for column in frame.columns}
    id_column = None
    mapped: list[str | None] = []
    for candidate in _CSV_ID_COLUMNS:
        column = lowered.get(candidate)
        if column is None:
            continue
        values = [str(value).strip() for value in frame[column].tolist()]
        attempt = [_resolve(value) for value in values]
        if any(item is not None for item in attempt):
            id_column, mapped = column, attempt
            break
    if id_column is None:
        raise FeatureSetBuildError(
            "the CSV has no column identifying cohort volumes (one of "
            f"{', '.join(_CSV_ID_COLUMNS)} holding volume UIDs, or an unambiguous "
            "single-volume subject key)"
        )
    unmatched = [
        str(frame[id_column].iloc[index])
        for index, volume in enumerate(mapped)
        if volume is None
    ]
    if unmatched:
        raise FeatureSetBuildError(
            f"{len(unmatched)} CSV row(s) are not labelled members of the cohort: "
            f"{unmatched[:10]}"
        )
    if len(set(mapped)) != len(mapped):
        raise FeatureSetBuildError(
            "two CSV rows belong to one volume; feature_matrix is keyed by "
            "volume_series_instance_uid"
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
    features.insert(0, TARGET_COLUMN, [targets[volume] for volume in mapped])
    features.insert(0, SUBJECT_KEY, [subjects[volume] for volume in mapped])
    features.insert(0, VOLUME_KEY, mapped)
    features["__ordinal"] = [first_ordinal[volume] for volume in mapped]
    features = features.sort_values(["__ordinal", VOLUME_KEY]).drop(columns="__ordinal")
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
    """`{volume_series_instance_uid: [value, …]}` in `feature_names` order, and
    the number of missing cells (recorded in `quality_summary`, never hidden).
    Keyed by volume, not subject: a patient may contribute more than one row."""
    names = check_feature_names(feature_names)
    missing_columns = [name for name in names if name not in frame.columns]
    if missing_columns:
        raise FeatureSetBuildError(f"feature(s) {missing_columns[:10]} are not in the table")
    matrix: dict[str, list[float | None]] = {}
    missing = 0
    for record in frame[[VOLUME_KEY, *names]].itertuples(index=False, name=None):
        volume = str(record[0])
        if volume in matrix:
            raise FeatureSetBuildError(f"volume {volume!r} appears twice in the table")
        values = [finite_or_none(value) for value in record[1:]]
        missing += sum(value is None for value in values)
        matrix[volume] = values
    if not matrix:
        raise FeatureSetBuildError("the feature matrix has no row")
    return matrix, missing


def assemble_rows(
    feature_names: Sequence[str],
    feature_matrix: Mapping[str, Sequence[float | None]],
    keys_in_ordinal_order: Iterable[str],
) -> list[list[float | None]]:
    """The reader's side of rule 2: rows in cohort order, columns in name order."""
    width = len(feature_names)
    rows = []
    for key in keys_in_ordinal_order:
        if key not in feature_matrix:
            continue
        values = list(feature_matrix[key])
        if len(values) != width:
            raise FeatureSetBuildError(f"row {key!r} is not rectangular")
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
    "VOLUME_KEY",
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
