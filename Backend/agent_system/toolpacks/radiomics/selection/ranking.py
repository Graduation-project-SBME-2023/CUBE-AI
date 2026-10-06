"""Feature importance: the selection method's ranking, and the alternative
rankings shown beside it. Every method fits on the training rows only."""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

import numpy as np
import pandas as pd

from ...common import ml_warnings
from ...common.values import finite_float
from .matrix import SelectionMatrix

logger = logging.getLogger(__name__)
ml_warnings.install()

METHODS = ("rf", "f_test", "mutual_info", "rfe")
DEFAULT_METHOD = "rf"
SEED = 42
MIN_ROWS_FOR_ALTERNATIVES = 5


def _forest(task_type: str, n_estimators: int, n_jobs: int) -> Any:
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor

    model = RandomForestClassifier if task_type == "classification" else RandomForestRegressor
    return model(n_estimators=n_estimators, random_state=SEED, n_jobs=n_jobs)


def _univariate(task_type: str, kind: str) -> Any:
    from sklearn.feature_selection import f_classif, f_regression, mutual_info_classif, mutual_info_regression

    if kind == "f_test":
        return f_classif if task_type == "classification" else f_regression
    estimator = mutual_info_classif if task_type == "classification" else mutual_info_regression
    # Seeded: mutual information is estimated with random noise, and the same
    # rows must rank the same way on a replay.
    return partial(estimator, random_state=SEED)


def _ranked(columns: pd.Index, scores: Any) -> pd.DataFrame:
    """``feature, importance`` rows, most important first."""
    frame = pd.DataFrame({"feature": columns, "importance": scores})
    return frame.sort_values("importance", ascending=False).reset_index(drop=True)


def importance(method: str, matrix: SelectionMatrix, n_jobs: int = 1) -> pd.DataFrame:
    """Every feature's importance under ``method``, most important first.
    RFE's ranking is negated, so its selected features come first."""
    from sklearn.feature_selection import RFE, SelectKBest

    X_fit, y_fit, task = matrix.X_fit, matrix.y_fit, matrix.task_type
    if method == "rf":
        scores = _forest(task, 100, n_jobs).fit(X_fit, y_fit).feature_importances_
    elif method == "rfe":
        selector = RFE(_forest(task, 100, n_jobs), n_features_to_select=matrix.X.shape[1], step=0.1)
        scores = -selector.fit(X_fit, y_fit).ranking_
    else:
        scores = SelectKBest(_univariate(task, method), k="all").fit(X_fit, y_fit).scores_
    return _ranked(matrix.X.columns, scores)


def rfe_selection(matrix: SelectionMatrix, n: int, n_jobs: int = 1) -> list[str]:
    """The ``n`` features RFE keeps (it refits for every ``n``)."""
    from sklearn.feature_selection import RFE

    n = min(n, matrix.X.shape[1])
    selector = RFE(_forest(matrix.task_type, 100, n_jobs), n_features_to_select=n, step=0.1)
    selector.fit(matrix.X_fit, matrix.y_fit)
    return matrix.X.columns[selector.get_support()].tolist()


def _rows(ranking: pd.DataFrame, top_n: int) -> list[dict[str, Any]]:
    return [
        {"feature": str(row.feature), "importance": finite_float(row.importance)}
        for row in ranking.head(min(top_n, len(ranking))).itertuples(index=False)
    ]


def _volume_normalised(matrix: SelectionMatrix, n_jobs: int) -> pd.DataFrame | None:
    """Importance after regressing out the lesion volume: what matters beyond size."""
    from sklearn.linear_model import LinearRegression

    volume_columns = [column for column in matrix.X.columns if "voxelvolume" in column.lower()]
    if not volume_columns:
        return None
    X, X_fit = matrix.X, matrix.X_fit
    residuals = X.copy()
    for column in X.columns:
        try:
            # Unsupervised, but fitted on the training rows only, so holdout
            # structure never shapes the feature the forest is scored on.
            model = LinearRegression().fit(X_fit[volume_columns].values, X_fit[column].values)
            residuals[column] = X[column].values - model.predict(X[volume_columns].values)
        except Exception:
            pass  # the column keeps its values
    forest = _forest(matrix.task_type, 50, n_jobs).fit(residuals.iloc[matrix.split.train_index], matrix.y_fit)
    return _ranked(X.columns, forest.feature_importances_)


def stability(matrix: SelectionMatrix, top_n: int) -> list[dict[str, Any]]:
    """A label-free stability proxy: 1 / (1 + coefficient of variation)."""
    X = matrix.X
    variation = (X.std() / (X.mean().abs() + 1e-9)).abs()
    frame = pd.DataFrame({"feature": X.columns, "stability": 1.0 / (1.0 + variation.values)})
    frame = frame.sort_values("stability", ascending=False).head(min(top_n, len(frame)))
    return [
        {"feature": str(row.feature), "stability": round(float(row.stability), 4)}
        for row in frame.itertuples(index=False)
    ]


def alternatives(matrix: SelectionMatrix, top_n: int, n_jobs: int = 1) -> tuple[dict[str, list], list[dict]]:
    """The ANOVA, mutual-information and volume-normalised rankings, and the
    stability scores. One that cannot be computed is left out."""
    from sklearn.feature_selection import SelectKBest

    if matrix.X_fit.shape[0] < MIN_ROWS_FOR_ALTERNATIVES or matrix.X.shape[1] < 2:
        return {}, []
    rankings: dict[str, list] = {}
    for name, kind in (("anova", "f_test"), ("mutual_info", "mutual_info")):
        try:
            scores = SelectKBest(_univariate(matrix.task_type, kind), k="all").fit(matrix.X_fit, matrix.y_fit).scores_
            rankings[name] = _rows(_ranked(matrix.X.columns, scores), top_n)
        except Exception as exc:
            logger.warning("%s ranking failed: %s", name, exc)
    try:
        normalised = _volume_normalised(matrix, n_jobs)
        if normalised is not None:
            rankings["normalized_by_volume"] = _rows(normalised, top_n)
    except Exception as exc:
        logger.warning("volume-normalised ranking failed: %s", exc)
    try:
        scores = stability(matrix, top_n)
    except Exception as exc:
        logger.warning("stability scores failed: %s", exc)
        scores = []
    return rankings, scores


def top_features(method: str, matrix: SelectionMatrix, ranking: pd.DataFrame, n: int, n_jobs: int = 1) -> list[str]:
    """The ``n`` features a method selects."""
    if method == "rfe":
        return rfe_selection(matrix, n, n_jobs)
    return [str(name) for name in ranking["feature"].iloc[: min(n, matrix.X.shape[1])]]


def importance_rows(ranking: pd.DataFrame, top_n: int) -> list[dict[str, Any]]:
    return _rows(ranking, top_n)


__all__ = [
    "DEFAULT_METHOD",
    "METHODS",
    "alternatives",
    "importance",
    "importance_rows",
    "rfe_selection",
    "stability",
    "top_features",
]
