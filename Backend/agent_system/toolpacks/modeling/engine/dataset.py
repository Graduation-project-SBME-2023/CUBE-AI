"""The training and evaluation matrices of one comparison, and predictions.

A classifier's labels are encoded as codes ``0 … k-1`` over the sorted labels
of the training and evaluation rows together, so XGBoost (which needs codes)
and every other estimator see the same target. ``classes`` maps them back.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from . import metrics
from .estimators import CLASSIFICATION
from .preprocessing import TARGET, FeatureColumns, PreprocessingConfig, feature_columns


@dataclass
class Dataset:
    task: str
    columns: FeatureColumns
    config: PreprocessingConfig
    X_train: pd.DataFrame
    y_train: np.ndarray  # codes for a classifier, floats for a regressor
    X_eval: pd.DataFrame
    y_eval: np.ndarray
    eval_frame: pd.DataFrame  # the evaluation rows as they arrived (features + target)
    classes: list[Any] | None  # classifier labels, by code

    @property
    def n_classes(self) -> int:
        return len(self.classes or ())

    @property
    def features(self) -> list[str]:
        return self.columns.used


def sorted_labels(values: Sequence[Any]) -> list[Any]:
    unique = list(pd.unique(pd.Series(list(values))))
    try:
        return sorted(unique)
    except TypeError:
        return sorted(unique, key=str)


def encode(labels: Any, classes: Sequence[Any]) -> np.ndarray:
    index = {label: code for code, label in enumerate(classes)}
    try:
        return np.asarray([index[label] for label in labels], dtype=int)
    except KeyError as exc:
        raise ValueError(f"label {exc.args[0]!r} is not one of the classes {list(classes)}") from None


def decode(codes: Any, classes: Sequence[Any]) -> list[Any]:
    return [classes[int(code)] for code in np.asarray(codes)]


def build(
    task: str,
    train: pd.DataFrame,
    holdout: pd.DataFrame,
    config: PreprocessingConfig,
    target: str = TARGET,
) -> Dataset:
    columns = feature_columns(train, config, target)
    missing = [name for name in columns.used if name not in holdout.columns]
    if missing:
        raise ValueError(f"the evaluation rows lack feature columns: {', '.join(missing)}")
    classes = None
    if task == CLASSIFICATION:
        classes = sorted_labels([*train[target], *holdout[target]])
        y_train = encode(train[target], classes)
        y_eval = encode(holdout[target], classes)
    else:
        y_train = train[target].to_numpy(dtype=float)
        y_eval = holdout[target].to_numpy(dtype=float)
    return Dataset(
        task=task,
        columns=columns,
        config=config,
        X_train=train[columns.used],
        y_train=y_train,
        X_eval=holdout[columns.used],
        y_eval=y_eval,
        eval_frame=holdout,
        classes=classes,
    )


def missing_features(frame: pd.DataFrame, features: Sequence[str]) -> list[str]:
    return [name for name in features if name not in frame.columns]


def predictions_frame(
    task: str,
    model: Any,
    frame: pd.DataFrame,
    features: Sequence[str],
    classes: Sequence[Any] | None,
) -> pd.DataFrame:
    """``frame`` plus ``prediction_label`` (the label, not its code) and, for
    a classifier with probabilities, ``prediction_score_<label>`` per class
    (one ``prediction_score`` of decision values for a binary model without
    them) — the columns the evaluation and refit agreement read."""
    X = frame[list(features)]
    out = frame.reset_index(drop=True).copy()
    predicted = model.predict(X)
    if task != CLASSIFICATION:
        out["prediction_label"] = np.asarray(predicted, dtype=float)
        return out
    out["prediction_label"] = decode(predicted, classes)
    scores = metrics.scores_of(model, X)
    if scores is not None and scores.ndim == 2 and scores.shape[1] == len(classes):
        for code, label in enumerate(classes):
            out[f"prediction_score_{label}"] = np.round(scores[:, code], 6)
    elif scores is not None and scores.ndim == 1 and len(classes) == 2:
        out["prediction_score"] = np.round(scores, 6)
    return out


def score_column(predictions: pd.DataFrame, positive: Any) -> str | None:
    """The positive class's score column of a predictions frame."""
    exact = f"prediction_score_{positive}"
    if exact in predictions.columns:
        return exact
    return "prediction_score" if "prediction_score" in predictions.columns else None


def bundle_classes(bundle: Mapping[str, Any]) -> list[Any] | None:
    classes = bundle.get("classes")
    return list(classes) if classes is not None else None


__all__ = [
    "Dataset",
    "build",
    "bundle_classes",
    "decode",
    "encode",
    "missing_features",
    "predictions_frame",
    "score_column",
    "sorted_labels",
]
