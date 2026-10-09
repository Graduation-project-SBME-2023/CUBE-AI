"""The labelled feature matrix feature selection fits on.

The label is the frozen cohort member's: a generic CSV's own label column is
dropped, so only one copy of a label exists. The selection holdout is planned
BEFORE anything is fitted and grouped by the cohort's ``subject_key``: a
patient's rows never straddle it, and every encoding or imputation that looks
at values fits on the training rows only.
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .. import digests as provenance
from ..feature_set_builder import SUBJECT_KEY, TARGET_COLUMN, VOLUME_KEY, frame_from_csv, wide_table_from_rows
from ..selection_split import SelectionSplit, plan_selection_split

logger = logging.getLogger(__name__)

ENCODINGS = ("auto", "onehot", "label", "target", "none")
# Columns that identify a row rather than describe it; never features.
IDENTIFIER_COLUMNS = frozenset({
    "id", "subject_id", "subjectid", "subject_key", "volume_series_instance_uid", "ordinal",
    "patientid", "patient_id", "case_id", "caseid", "name", "uid", "pat_id", "unnamed: 0",
})
CLASSIFICATION_UNIQUE_RATIO = 0.05
CLASSIFICATION_MAX_CLASSES = 10


class SelectionInputError(ValueError):
    """The input cannot be selected from."""


@dataclass
class SelectionSource:
    """The table selection reads, and its identity."""

    frame: pd.DataFrame  # volume, subject, target, then the features
    kind: str  # "radiomics_features" | "input_csv"
    source_feature_ids: list[str] | None = None
    source_digest: str | None = None  # None: the API derives it from the rows
    config_digests: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def source_from_rows(rows: list[dict[str, Any]], analysis_rows: list[dict[str, Any]]) -> SelectionSource:
    """The run's recorded radiomics rows, labelled from the cohort."""
    table = wide_table_from_rows(rows, analysis_rows)
    return SelectionSource(
        frame=table.frame,
        kind="radiomics_features",
        source_feature_ids=table.source_feature_ids,
        config_digests=sorted({row["config_digest"] for row in rows if row.get("config_digest")}),
    )


def source_from_csv(path: str, analysis_rows: list[dict[str, Any]]) -> SelectionSource:
    """A generic CSV, re-keyed by volume and labelled from the cohort; its exact
    bytes are its source identity. Reads the file: run it off the event loop."""
    if not os.path.exists(path):
        raise SelectionInputError(f"Input file not found: {path}")
    with open(path, "rb") as stream:
        payload = stream.read()
    frame, notes = frame_from_csv(pd.read_csv(io.BytesIO(payload)), analysis_rows)
    return SelectionSource(
        frame=frame, kind="input_csv", source_digest=provenance.bytes_digest(payload), notes=notes
    )


def is_identifier(column: str) -> bool:
    lowered = str(column).lower()
    return lowered in IDENTIFIER_COLUMNS or lowered.endswith("_id")


def task_type_of(y: pd.Series, requested: str | None) -> str:
    """The requested task, else classification for a categorical or
    few-valued target, regression otherwise."""
    if requested:
        return requested
    if y.dtype == "object" or y.dtype.name == "category":
        return "classification"
    unique = y.nunique()
    if unique / len(y) < CLASSIFICATION_UNIQUE_RATIO or unique < CLASSIFICATION_MAX_CLASSES:
        return "classification"
    return "regression"


@dataclass
class SelectionMatrix:
    X: pd.DataFrame  # encoded and imputed, every row
    y: pd.Series
    task_type: str
    split: SelectionSplit
    encodings: dict[str, Any]
    source: pd.DataFrame  # the measured values, for the frozen sets

    @property
    def X_fit(self) -> pd.DataFrame:
        return self.X.iloc[self.split.train_index]

    @property
    def y_fit(self) -> pd.Series:
        return self.y.iloc[self.split.train_index]

    @property
    def volume_keys(self) -> list[str]:
        return self.source[VOLUME_KEY].astype(str).tolist()

    def values_of(self, name: str) -> Any:
        """A feature's values: measured when it was measured, else encoded."""
        return (self.source[name] if name in self.source.columns else self.X[name]).values


def _encode(
    X: pd.DataFrame, y: pd.Series, train: list[int], encode: str, max_onehot: int
) -> tuple[pd.DataFrame, dict[str, Any]]:
    categorical = X.select_dtypes(include=["object", "category"]).columns.tolist()
    if not categorical:
        return X, {}
    if encode == "none":
        return X.select_dtypes(include=[np.number]), {}
    encodings: dict[str, Any] = {}
    original = X.copy()
    for column in categorical:
        method = encode
        if method == "auto":
            method = "onehot" if X[column].nunique() <= max_onehot else "label"
        if method == "onehot":
            X = pd.concat([X, pd.get_dummies(X[column], prefix=column, drop_first=True)], axis=1)
            encodings[column] = {"method": "onehot", "categories": original[column].unique().tolist()}
        elif method == "label":
            classes = sorted(X[column].astype(str).unique())
            codes = {value: code for code, value in enumerate(classes)}
            X[f"{column}_label"] = X[column].astype(str).map(codes)
            encodings[column] = {"method": "label", "mapping": codes}
        elif method == "target":
            # Fitted on training rows only: a target encoding fitted on the
            # holdout writes those labels straight into a feature column.
            fitted = pd.concat([X[[column]], y], axis=1).iloc[train]
            X[f"{column}_target"] = X[column].map(fitted.groupby(column)[TARGET_COLUMN].mean())
            X[f"{column}_freq"] = X[column].map(X[column].iloc[train].value_counts(normalize=True))
            encodings[column] = {"method": "target", "fitted_on": "train"}
    return X.drop(columns=categorical), encodings


def build_matrix(
    frame: pd.DataFrame,
    *,
    task_type: str | None,
    encode: str,
    max_onehot_cardinality: int,
    holdout_size: float,
    split_seed: int,
) -> SelectionMatrix:
    """The encoded, imputed matrix and its selection split."""
    X = frame.drop(columns=[TARGET_COLUMN])
    X = X.drop(columns=[column for column in X.columns if is_identifier(column)])
    y = frame[TARGET_COLUMN]
    if X.shape[1] == 0:
        raise SelectionInputError("No usable feature columns remain after removing identifiers and the target column.")
    if y.isna().any():
        raise SelectionInputError("The target column contains missing values.")

    task = task_type_of(y, task_type)
    split = plan_selection_split(
        n_rows=len(frame),
        labels=y.tolist(),
        group_values=frame[SUBJECT_KEY].astype(str).tolist(),
        group_column=SUBJECT_KEY,
        task_type=task,
        holdout_size=holdout_size,
        seed=split_seed,
    )
    if split.label_supervised_on_full_data:
        logger.warning(
            "Feature selection is fitted on every row (%s). Downstream holdout metrics "
            "are optimistic; this is recorded in the output.",
            split.degraded_reason,
        )
    X, encodings = _encode(X, y, split.train_index, encode, max_onehot_cardinality)
    # Medians come from the training rows only: a median over the holdout is
    # a summary of held-out data leaking back into every imputed cell.
    numeric = X.select_dtypes(include=[np.number]).columns
    X[numeric] = X[numeric].fillna(X[numeric].iloc[split.train_index].median())
    X = X.fillna(0)
    logger.info(
        "Feature matrix: %d rows x %d features (selection fitted on %d row(s))",
        X.shape[0], X.shape[1], len(split.train_index),
    )
    return SelectionMatrix(X=X, y=y, task_type=task, split=split, encodings=encodings, source=frame)


__all__ = [
    "ENCODINGS",
    "SelectionInputError",
    "SelectionMatrix",
    "SelectionSource",
    "build_matrix",
    "is_identifier",
    "source_from_csv",
    "source_from_rows",
    "task_type_of",
]
