"""The ``chart_data`` the Features view draws, and the label-free QC a frozen
feature set stores. The ranking is label-derived: it goes back to the agent
and the view, and is never stored as feature-set analysis."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from ...common.values import finite_float, json_safe
from ..feature_set_builder import FEATURE_SET_SCHEMA_VERSION
from .matrix import SelectionMatrix
from .ranking import SEED, importance_rows

logger = logging.getLogger(__name__)

CORRELATION_FEATURES = 20
MAX_CUMULATIVE_POINTS = 300
MAX_TSNE_ROWS = 5000
MAX_STABILITY_ROWS = 50


def correlation(X: pd.DataFrame, features: list[str]) -> dict[str, Any] | None:
    """The correlation matrix of ``features`` (at least two), or ``None``."""
    columns = [name for name in features if name in X.columns]
    if len(columns) < 2:
        return None
    matrix = X[columns].corr()
    return {
        "labels": columns,
        "matrix": [[finite_float(matrix.iloc[r, c]) for c in range(len(columns))] for r in range(len(columns))],
    }


def cumulative(ranking: pd.DataFrame) -> list[dict[str, Any]]:
    """Cumulative importance (%) by number of features, at most ~300 points."""
    total = ranking["importance"].sum()
    running = ranking["importance"].cumsum()
    percent = running / total * 100 if total > 0 else running * 0
    step = max(1, len(percent) // MAX_CUMULATIVE_POINTS)
    last = len(percent) - 1
    return [
        {"n_features": index + 1, "cumulative_pct": round(float(value), 3)}
        for index, value in enumerate(percent)
        if index % step == 0 or index == last
    ]


def _points(coordinates: Any, labels: pd.Series) -> list[dict[str, Any]]:
    return [
        {"x": round(float(x), 4), "y": round(float(y), 4), "target": str(target)}
        for (x, y), target in zip(coordinates, labels)
    ]


def pca(X: pd.DataFrame, y: pd.Series) -> dict[str, Any] | None:
    from sklearn.decomposition import PCA

    try:
        model = PCA(n_components=2, random_state=SEED)
        points = model.fit_transform(X)
    except Exception as exc:
        logger.warning("PCA failed: %s", exc)
        return None
    return {
        "points": _points(points, y),
        "explained_variance": [round(float(v), 4) for v in model.explained_variance_ratio_],
        "n_features": X.shape[1],
    }


def tsne(X: pd.DataFrame, y: pd.Series, n_jobs: int) -> dict[str, Any] | None:
    from sklearn.manifold import TSNE

    if len(X) > MAX_TSNE_ROWS:
        return None
    try:
        points = TSNE(
            n_components=2, perplexity=min(30, len(X) - 1), random_state=SEED, n_jobs=n_jobs
        ).fit_transform(X)
    except Exception as exc:
        logger.warning("t-SNE failed: %s", exc)
        return None
    return {"points": _points(points, y), "n_features": X.shape[1]}


def chart_data(
    matrix: SelectionMatrix,
    ranking: pd.DataFrame,
    *,
    method: str,
    top_n_plot: int,
    scatter_features: int,
    n_jobs: int = 1,
) -> dict[str, Any]:
    """Importance bars, the cumulative curve, the top-20 correlation and the
    PCA / t-SNE projections of the top ``scatter_features`` features."""
    X, y = matrix.X, matrix.y
    ranked = ranking["feature"].tolist()
    scatter = [name for name in ranked[: min(scatter_features, X.shape[1])] if name in X.columns]
    data: dict[str, Any] = {
        "task_type": matrix.task_type,
        "method": method,
        "importance_bars": importance_rows(ranking, top_n_plot),
        "cumulative": cumulative(ranking),
        "correlation": correlation(X, ranked[:CORRELATION_FEATURES]),
        "pca": None,
        "tsne": None,
    }
    if len(scatter) >= 2:
        data["pca"] = pca(X[scatter], y)
        data["tsne"] = tsne(X[scatter], y, n_jobs)
    return json_safe(data)


def label_free_qc(X: pd.DataFrame, names: list[str], stability: list[dict[str, Any]]) -> dict[str, Any]:
    """``feature_sets.analysis``: label-free QC only (correlation and stability)."""
    columns = [name for name in names if name in X.columns]
    qc: dict[str, Any] = {"schema_version": FEATURE_SET_SCHEMA_VERSION, "feature_count": len(names)}
    matrix = correlation(X, columns[:CORRELATION_FEATURES])
    if matrix is not None:
        qc["correlation"] = matrix
    wanted = set(columns)
    qc["stability"] = [item for item in stability if item.get("feature") in wanted][:MAX_STABILITY_ROWS]
    return json_safe(qc)


__all__ = ["chart_data", "correlation", "cumulative", "label_free_qc", "pca", "tsne"]
