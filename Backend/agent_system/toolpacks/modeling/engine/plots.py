"""The figures of a fitted model, as PNG files in one directory per variant.

Drawn on ``matplotlib.figure.Figure`` with the Agg canvas, never pyplot, so no
global backend or figure state is touched. A figure that cannot be drawn for a
model (no probabilities, no importances, not a tree) is skipped, and the
reason is returned.

- classification: ROC curve (``AUC.png``), confusion matrix, feature importance;
- regression: residuals, prediction error, feature importance;
- both: a SHAP summary (mean |SHAP value| per feature) for tree models, which
  replaces PyCaret's ``interpret_model``.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from . import metrics
from .estimators import CLASSIFICATION

MAX_FEATURES_SHOWN = 20
SHAP_MAX_ROWS = 200


def _figure(width: float = 6.4, height: float = 4.8) -> Any:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(width, height), dpi=100)
    FigureCanvasAgg(figure)
    return figure


def _save(figure: Any, plots_dir: str, name: str) -> str:
    path = os.path.join(plots_dir, f"{name}.png")
    figure.tight_layout()
    figure.savefig(path, format="png")
    return path


def feature_names(model: Any) -> list[str]:
    """The names of the columns the estimator sees, without the
    ``num__`` / ``cat__`` prefixes of the column transformer."""
    names = model[:-1].get_feature_names_out()
    return [str(name).split("__", 1)[-1] for name in names]


def importances(model: Any) -> np.ndarray | None:
    estimator = model.steps[-1][1] if getattr(model, "steps", None) else model
    values = getattr(estimator, "feature_importances_", None)
    if values is None and getattr(estimator, "coef_", None) is not None:
        coef = np.asarray(estimator.coef_, dtype=float)
        values = np.abs(coef).mean(axis=0) if coef.ndim == 2 else np.abs(coef)
    return None if values is None else np.asarray(values, dtype=float)


def _bar(plots_dir: str, name: str, title: str, labels: Sequence[str], values: np.ndarray) -> str:
    order = np.argsort(values)[::-1][:MAX_FEATURES_SHOWN][::-1]
    figure = _figure(7.5, max(3.0, 0.3 * len(order) + 1.2))
    axes = figure.add_subplot()
    axes.barh([labels[i] for i in order], values[order], color="#4C72B0")
    axes.set_title(title)
    return _save(figure, plots_dir, name)


def feature_importance_plot(model: Any, plots_dir: str) -> str:
    values = importances(model)
    if values is None:
        raise ValueError("the estimator reports no feature importances")
    names = feature_names(model)
    if len(names) != len(values):
        raise ValueError("the importances do not match the transformed features")
    return _bar(plots_dir, "Feature Importance", "Feature Importance", names, values)


def shap_summary_plot(model: Any, X: pd.DataFrame, plots_dir: str, *, seed: int = 0) -> str:
    import shap

    sample = X.sample(n=min(len(X), SHAP_MAX_ROWS), random_state=seed) if len(X) > SHAP_MAX_ROWS else X
    transformed = model[:-1].transform(sample)
    values = shap.TreeExplainer(model.steps[-1][1]).shap_values(transformed)
    if isinstance(values, list):  # one array per class
        values = np.mean([np.abs(v) for v in values], axis=0) if len(values) > 2 else np.abs(values[-1])
    values = np.abs(np.asarray(values, dtype=float))
    if values.ndim == 3:  # (rows, features, classes)
        values = values[:, :, -1] if values.shape[2] == 2 else values.mean(axis=2)
    return _bar(
        plots_dir, "SHAP Summary", "SHAP summary (mean |SHAP value|)",
        feature_names(model), values.mean(axis=0),
    )


def roc_plot(scores: np.ndarray, y: np.ndarray, classes: Sequence[Any], plots_dir: str) -> str:
    from sklearn.metrics import auc, roc_curve

    figure = _figure()
    axes = figure.add_subplot()
    if len(classes) == 2:
        fpr, tpr, _ = roc_curve(y, metrics.positive_scores(scores), pos_label=1)
        axes.plot(fpr, tpr, label=f"{classes[1]} (AUC = {auc(fpr, tpr):.3f})")
    else:
        if scores.ndim != 2:
            raise ValueError("a multiclass ROC curve needs per-class scores")
        for code, label in enumerate(classes):
            if (y == code).any() and (y != code).any():
                fpr, tpr, _ = roc_curve(y == code, scores[:, code])
                axes.plot(fpr, tpr, label=f"{label} (AUC = {auc(fpr, tpr):.3f})")
    axes.plot([0, 1], [0, 1], linestyle="--", color="grey")
    axes.set_xlabel("False positive rate")
    axes.set_ylabel("True positive rate")
    axes.set_title("ROC curve")
    axes.legend(loc="lower right")
    return _save(figure, plots_dir, "AUC")


def confusion_matrix_plot(y: np.ndarray, predicted: np.ndarray, classes: Sequence[Any], plots_dir: str) -> str:
    from sklearn.metrics import confusion_matrix

    matrix = confusion_matrix(y, predicted, labels=list(range(len(classes))))
    figure = _figure(5.5, 4.8)
    axes = figure.add_subplot()
    image = axes.imshow(matrix, cmap="Blues")
    figure.colorbar(image, ax=axes)
    ticks = range(len(classes))
    axes.set_xticks(ticks, [str(c) for c in classes])
    axes.set_yticks(ticks, [str(c) for c in classes])
    threshold = matrix.max() / 2 if matrix.size else 0
    for row in ticks:
        for column in ticks:
            axes.text(column, row, int(matrix[row, column]), ha="center", va="center",
                      color="white" if matrix[row, column] > threshold else "black")
    axes.set_xlabel("Predicted")
    axes.set_ylabel("True")
    axes.set_title("Confusion Matrix")
    return _save(figure, plots_dir, "Confusion Matrix")


def residuals_plot(y: np.ndarray, predicted: np.ndarray, plots_dir: str) -> str:
    figure = _figure()
    axes = figure.add_subplot()
    axes.scatter(predicted, y - predicted, alpha=0.7)
    axes.axhline(0, linestyle="--", color="grey")
    axes.set_xlabel("Predicted value")
    axes.set_ylabel("Residual")
    axes.set_title("Residuals")
    return _save(figure, plots_dir, "Residuals")


def prediction_error_plot(y: np.ndarray, predicted: np.ndarray, plots_dir: str) -> str:
    figure = _figure()
    axes = figure.add_subplot()
    axes.scatter(y, predicted, alpha=0.7)
    low, high = float(min(y.min(), predicted.min())), float(max(y.max(), predicted.max()))
    axes.plot([low, high], [low, high], linestyle="--", color="grey")
    axes.set_xlabel("True value")
    axes.set_ylabel("Predicted value")
    axes.set_title("Prediction Error")
    return _save(figure, plots_dir, "Prediction Error")


def draw_all(
    task: str,
    model: Any,
    X_eval: pd.DataFrame,
    y_eval: np.ndarray,
    plots_dir: str,
    *,
    classes: Sequence[Any] | None = None,
    tree: bool = False,
    seed: int = 0,
) -> tuple[list[str], list[str]]:
    """Every figure that applies; returns (paths, reasons for the skipped ones)."""
    os.makedirs(plots_dir, exist_ok=True)
    predicted = np.asarray(model.predict(X_eval))
    drawers: list[tuple[str, Any]] = []
    if task == CLASSIFICATION:
        scores = metrics.scores_of(model, X_eval)
        if scores is not None:
            drawers.append(("roc", lambda: roc_plot(scores, y_eval, classes, plots_dir)))
        drawers.append(("confusion_matrix", lambda: confusion_matrix_plot(y_eval, predicted, classes, plots_dir)))
    else:
        y_float = np.asarray(y_eval, dtype=float)
        predicted_float = predicted.astype(float)
        drawers.append(("residuals", lambda: residuals_plot(y_float, predicted_float, plots_dir)))
        drawers.append(("prediction_error", lambda: prediction_error_plot(y_float, predicted_float, plots_dir)))
    if getattr(model, "steps", None):
        drawers.append(("feature_importance", lambda: feature_importance_plot(model, plots_dir)))
        if tree:
            drawers.append(("shap_summary", lambda: shap_summary_plot(model, X_eval, plots_dir, seed=seed)))
    paths: list[str] = []
    skipped: list[str] = []
    for name, draw in drawers:
        try:
            paths.append(draw())
        except Exception as exc:
            skipped.append(f"{name}: {exc}"[:300])
    return paths, skipped


__all__ = [
    "confusion_matrix_plot",
    "draw_all",
    "feature_importance_plot",
    "feature_names",
    "importances",
    "prediction_error_plot",
    "residuals_plot",
    "roc_plot",
    "shap_summary_plot",
]
