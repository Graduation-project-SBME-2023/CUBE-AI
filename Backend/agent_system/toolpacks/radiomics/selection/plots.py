"""The archival figures of a selection, published as ``figure_png`` artifacts
for the report. Drawn on ``matplotlib.figure.Figure`` with the Agg canvas —
never pyplot, whose global state is shared by every session in the process.
A figure that cannot be drawn is skipped."""

from __future__ import annotations

import logging
import os
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DPI = 300
THRESHOLDS = ((0.8, "red"), (0.9, "green"), (0.95, "orange"), (0.99, "purple"))
CORRELATION_FEATURES = 20

IMPORTANCE_FIGURE = "feature_importance_plot.png"
CUMULATIVE_FIGURE = "cumulative_importance_plot.png"
CORRELATION_FIGURE = "feature_correlation_heatmap.png"


def _figure(width: float, height: float) -> Any:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(width, height))
    FigureCanvasAgg(figure)
    return figure


def _save(figure: Any, directory: str, name: str) -> str:
    path = os.path.join(directory, name)
    figure.tight_layout()
    figure.savefig(path, dpi=DPI, bbox_inches="tight")
    return path


def importance_bars(ranking: pd.DataFrame, directory: str, *, top_n: int, method: str) -> str:
    shown = ranking.head(min(top_n, len(ranking)))
    figure = _figure(12, max(4, 0.3 * len(shown) + 1.5))
    axes = figure.add_subplot()
    axes.barh(shown["feature"][::-1], shown["importance"][::-1], color="#4C72B0")
    axes.set_xlabel("importance")
    axes.set_title(f"Top {len(shown)} Feature Importance ({method})")
    return _save(figure, directory, IMPORTANCE_FIGURE)


def cumulative_curve(ranking: pd.DataFrame, directory: str) -> str:
    share = ranking["importance"].cumsum() / ranking["importance"].sum()
    figure = _figure(10, 6)
    axes = figure.add_subplot()
    axes.plot(range(1, len(share) + 1), share)
    for threshold, color in THRESHOLDS:
        count = int((share >= threshold).argmax()) + 1
        axes.axhline(threshold, color=color, linestyle="--", label=f"{threshold * 100:.0f}%: {count} features")
    axes.set_xlabel("Number of Features")
    axes.set_ylabel("Cumulative Importance")
    axes.set_title("Cumulative Feature Importance")
    axes.legend(loc="lower right")
    return _save(figure, directory, CUMULATIVE_FIGURE)


def correlation_heatmap(X: pd.DataFrame, features: list[str], directory: str) -> str:
    matrix = X[features].corr()
    values = np.ma.masked_array(matrix.values, mask=np.triu(np.ones_like(matrix.values, dtype=bool)))
    figure = _figure(12, 10)
    axes = figure.add_subplot()
    image = axes.imshow(values, cmap="RdBu_r", vmin=-1, vmax=1)
    figure.colorbar(image, ax=axes)
    ticks = range(len(features))
    axes.set_xticks(ticks, features, rotation=90)
    axes.set_yticks(ticks, features)
    for row in ticks:
        for column in range(row):
            axes.text(column, row, f"{matrix.iloc[row, column]:.2f}", ha="center", va="center", fontsize=7)
    axes.set_title("Correlation Matrix – Top 20 Features")
    return _save(figure, directory, CORRELATION_FIGURE)


def draw_all(X: pd.DataFrame, ranking: pd.DataFrame, directory: str, *, top_n: int, method: str) -> list[str]:
    """Every figure that can be drawn; returns their paths."""
    os.makedirs(directory, exist_ok=True)
    top = [name for name in ranking["feature"].iloc[:CORRELATION_FEATURES] if name in X.columns]
    drawers = [
        ("importance", lambda: importance_bars(ranking, directory, top_n=top_n, method=method)),
        ("cumulative", lambda: cumulative_curve(ranking, directory)),
    ]
    if len(top) >= 2:
        drawers.append(("correlation", lambda: correlation_heatmap(X, top, directory)))
    paths = []
    for name, draw in drawers:
        try:
            paths.append(draw())
        except Exception as exc:
            logger.warning("%s figure failed: %s", name, exc)
    return paths


__all__ = ["draw_all"]
