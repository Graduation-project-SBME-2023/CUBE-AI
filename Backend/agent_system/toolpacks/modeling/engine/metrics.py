"""The metrics of the result tables, under the names they always had.

Classification: ``Accuracy``, ``AUC``, ``Recall``, ``Prec.``, ``F1``,
``Kappa``, ``MCC``. Regression: ``MAE``, ``MSE``, ``RMSE``, ``R2``, ``RMSLE``,
``MAPE``. The success result shape and ``ModelingView`` read these names.

A classifier is scored on label codes ``0 … k-1`` (the sorted classes); a
binary classifier's positive class is code 1, the larger label.
"""

from __future__ import annotations

import math
import warnings
from typing import Any

import numpy as np

from .estimators import CLASSIFICATION

CLASSIFICATION_METRICS = ("Accuracy", "AUC", "Recall", "Prec.", "F1", "Kappa", "MCC")
REGRESSION_METRICS = ("MAE", "MSE", "RMSE", "R2", "RMSLE", "MAPE")
# What compare sorts by, best first (PyCaret's defaults).
SORT_METRIC = {CLASSIFICATION: "Accuracy", "regression": "R2"}
LOWER_IS_BETTER = frozenset({"MAE", "MSE", "RMSE", "RMSLE", "MAPE"})


def metric_names(task: str) -> tuple[str, ...]:
    return CLASSIFICATION_METRICS if task == CLASSIFICATION else REGRESSION_METRICS


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 6) if math.isfinite(number) else None


def scores_of(model: Any, X: Any) -> np.ndarray | None:
    """Class probabilities when the model gives them, else decision values,
    else None (a hard-voting blend)."""
    if hasattr(model, "predict_proba"):
        try:
            return np.asarray(model.predict_proba(X), dtype=float)
        except Exception:
            pass
    if hasattr(model, "decision_function"):
        try:
            return np.asarray(model.decision_function(X), dtype=float)
        except Exception:
            pass
    return None


def positive_scores(scores: np.ndarray | None) -> np.ndarray | None:
    """The positive class's score of a binary classifier."""
    if scores is None:
        return None
    return scores[:, -1] if scores.ndim == 2 else scores


def classification_scores(
    y_true: Any, y_pred: Any, scores: np.ndarray | None, n_classes: int
) -> dict[str, float | None]:
    from sklearn.metrics import (
        accuracy_score,
        cohen_kappa_score,
        f1_score,
        matthews_corrcoef,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    binary = n_classes == 2
    average = "binary" if binary else "weighted"
    labels = list(range(n_classes))
    auc = None
    if scores is not None and len(np.unique(y_true)) > 1:
        try:
            if binary:
                auc = roc_auc_score(y_true, positive_scores(scores))
            elif scores.ndim == 2 and np.allclose(scores.sum(axis=1), 1.0, atol=1e-3):
                auc = roc_auc_score(y_true, scores, multi_class="ovr", average="weighted", labels=labels)
        except ValueError:
            auc = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        kwargs: dict[str, Any] = {"average": average, "zero_division": 0}
        if binary:
            kwargs["pos_label"] = 1
        else:
            kwargs["labels"] = labels
        return {
            "Accuracy": _number(accuracy_score(y_true, y_pred)),
            "AUC": _number(auc),
            "Recall": _number(recall_score(y_true, y_pred, **kwargs)),
            "Prec.": _number(precision_score(y_true, y_pred, **kwargs)),
            "F1": _number(f1_score(y_true, y_pred, **kwargs)),
            "Kappa": _number(cohen_kappa_score(y_true, y_pred)),
            "MCC": _number(matthews_corrcoef(y_true, y_pred)),
        }


def regression_scores(y_true: Any, y_pred: Any) -> dict[str, float | None]:
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    mse = mean_squared_error(y_true, y_pred)
    rmsle = None
    if (y_true >= 0).all():
        rmsle = math.sqrt(np.mean((np.log1p(np.clip(y_pred, 0, None)) - np.log1p(y_true)) ** 2))
    nonzero = y_true != 0
    mape = (
        float(np.mean(np.abs((y_true[nonzero] - y_pred[nonzero]) / y_true[nonzero])))
        if nonzero.any()
        else None
    )
    r2 = r2_score(y_true, y_pred) if len(y_true) > 1 else None
    return {
        "MAE": _number(mean_absolute_error(y_true, y_pred)),
        "MSE": _number(mse),
        "RMSE": _number(math.sqrt(mse)),
        "R2": _number(r2),
        "RMSLE": _number(rmsle),
        "MAPE": _number(mape),
    }


def mean_scores(folds: list[dict[str, float | None]], task: str) -> dict[str, float | None]:
    """The mean of each metric over the folds that could compute it."""
    means: dict[str, float | None] = {}
    for name in metric_names(task):
        values = [fold[name] for fold in folds if fold.get(name) is not None]
        means[name] = _number(np.mean(values)) if values else None
    return means


def sort_key(task: str, metric: str | None = None):
    """A key that orders metric rows best first, missing values last."""
    metric = metric or SORT_METRIC[task]
    lower = metric in LOWER_IS_BETTER

    def key(row: dict[str, Any]) -> tuple[int, float]:
        value = row.get(metric)
        if value is None:
            return (1, 0.0)
        return (0, value if lower else -value)

    return key


def better(task: str, candidate: dict[str, Any], incumbent: dict[str, Any]) -> bool:
    """Whether ``candidate`` beats ``incumbent`` on the sort metric."""
    key = sort_key(task)
    return key(candidate) < key(incumbent)


__all__ = [
    "CLASSIFICATION_METRICS",
    "LOWER_IS_BETTER",
    "REGRESSION_METRICS",
    "SORT_METRIC",
    "better",
    "classification_scores",
    "mean_scores",
    "metric_names",
    "positive_scores",
    "regression_scores",
    "scores_of",
    "sort_key",
]
