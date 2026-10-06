"""Fitting and cross-validated scoring, shared by compare, tune and blend.

Class imbalance is handled with balanced sample weights (``fix_imbalance``)
for every estimator that accepts them; PyCaret used SMOTE, which needs
imbalanced-learn and at least six minority rows per training fold.

The libraries' routine warnings are silenced by ``ml_warnings.install()``, once for
the process, not per fit (``warnings.catch_warnings`` is not thread-safe).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ...common import ml_warnings
from . import metrics
from .estimators import CLASSIFICATION

ml_warnings.install()


def _final_step(model: Any) -> Any:
    steps = getattr(model, "steps", None)
    return steps[-1][1] if steps else model


def fit_params(model: Any, y: Any, *, balance: bool) -> dict[str, Any]:
    """``model__sample_weight`` with balanced weights, when asked for and the
    pipeline's estimator accepts it; a blend is fitted unweighted."""
    if not balance or not getattr(model, "steps", None):
        return {}
    from sklearn.utils.class_weight import compute_sample_weight
    from sklearn.utils.validation import has_fit_parameter

    if not has_fit_parameter(_final_step(model), "sample_weight"):
        return {}
    return {"model__sample_weight": compute_sample_weight("balanced", np.asarray(y))}


def supports_balance(model: Any) -> bool:
    from sklearn.utils.validation import has_fit_parameter

    return bool(getattr(model, "steps", None)) and has_fit_parameter(_final_step(model), "sample_weight")


def fit(model: Any, X: pd.DataFrame, y: Any, *, balance: bool = False) -> Any:
    model.fit(X, y, **fit_params(model, y, balance=balance))
    return model


def score(task: str, model: Any, X: pd.DataFrame, y: Any, n_classes: int = 0) -> dict[str, Any]:
    """The task's metrics of a fitted model on ``X``."""
    predicted = model.predict(X)
    if task == CLASSIFICATION:
        return metrics.classification_scores(y, predicted, metrics.scores_of(model, X), n_classes)
    return metrics.regression_scores(y, predicted)


def cv_scores(
    task: str,
    model: Any,
    X: pd.DataFrame,
    y: Any,
    cv: Any,
    *,
    balance: bool = False,
    n_classes: int = 0,
) -> dict[str, Any]:
    """The mean of each metric over the folds of ``cv``. ``model`` is cloned
    for every fold and is not fitted itself. A fold that fails fails the call."""
    from sklearn.base import clone

    y = np.asarray(y)
    stratify_on = y if task == CLASSIFICATION else None
    folds: list[dict[str, Any]] = []
    for train_index, test_index in cv.split(X, stratify_on):
        fold_model = fit(clone(model), X.iloc[train_index], y[train_index], balance=balance)
        folds.append(score(task, fold_model, X.iloc[test_index], y[test_index], n_classes))
    return metrics.mean_scores(folds, task)


__all__ = ["cv_scores", "fit", "fit_params", "score", "supports_balance"]
