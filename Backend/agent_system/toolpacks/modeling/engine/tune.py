"""Tune: a seeded random search over the registry's search space.

The candidates are drawn by ``ParameterSampler`` (what ``RandomizedSearchCV``
draws from) with a fixed ``n_iter``, and every candidate is scored with the
same cross-validation and metrics as the comparison, so a tuned row and a
compared row mean the same thing. As PyCaret's ``tune_model`` did by default,
the untuned model is kept when no candidate beats it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import fitting, metrics
from .estimators import Estimator

N_ITER = 10


@dataclass
class Tuned:
    model: Any  # unfitted pipeline with the chosen parameters
    cv: dict[str, Any]
    params: dict[str, Any] = field(default_factory=dict)  # estimator parameters that changed
    improved: bool = False  # False: the untuned model was kept
    tried: int = 0


def search_size(space: dict[str, Any]) -> int:
    return math.prod(len(values) for values in space.values()) if space else 0


def tune(
    task: str,
    model: Any,
    entry: Estimator,
    X: pd.DataFrame,
    y: Any,
    *,
    cv: Any,
    seed: int,
    base_cv: dict[str, Any],
    n_iter: int = N_ITER,
    balance: bool = False,
    n_classes: int = 0,
) -> Tuned:
    """``model`` is the candidate's unfitted pipeline and ``base_cv`` its
    compared scores. A candidate parameter set that fails is skipped."""
    from sklearn.base import clone
    from sklearn.model_selection import ParameterSampler

    space = {key: list(values) for key, values in entry.search_space.items()}
    best = Tuned(model=clone(model), cv=dict(base_cv))
    if not space:
        return best
    draws = ParameterSampler(space, n_iter=min(int(n_iter), search_size(space)), random_state=int(seed))
    for params in draws:
        candidate = clone(model).set_params(**{f"model__{key}": value for key, value in params.items()})
        best.tried += 1
        try:
            scores = fitting.cv_scores(task, candidate, X, y, cv, balance=balance, n_classes=n_classes)
        except Exception:
            continue
        if metrics.better(task, scores, best.cv):
            best = Tuned(model=candidate, cv=scores, params=dict(params), improved=True, tried=best.tried)
    return best


__all__ = ["N_ITER", "Tuned", "search_size", "tune"]
