"""Evaluation metrics of the modeling tools — dependency-light."""

from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np
import pandas as pd


def safe_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return None if np.isnan(number) or np.isinf(number) else round(number, 6)
    except Exception:
        return None


def _finite(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def compute_roc(y_true, y_prob, pos_label=None) -> Optional[dict]:
    """Return {fpr, tpr} lists for an ROC curve, or None."""
    try:
        from sklearn.metrics import roc_curve

        fpr, tpr, _ = roc_curve(y_true, y_prob, pos_label=pos_label)
        # Down-sample to 200 points max to keep payload small
        step = max(1, len(fpr) // 200)
        return {
            "fpr": [round(float(x), 5) for x in fpr[::step]],
            "tpr": [round(float(x), 5) for x in tpr[::step]],
        }
    except Exception:
        return None


def compute_pr(y_true, y_prob, pos_label=None) -> Optional[dict]:
    """Return {precision, recall} lists for a PR curve, or None."""
    try:
        from sklearn.metrics import precision_recall_curve

        prec, rec, _ = precision_recall_curve(y_true, y_prob, pos_label=pos_label)
        step = max(1, len(prec) // 200)
        return {
            "precision": [round(float(x), 5) for x in prec[::step]],
            "recall": [round(float(x), 5) for x in rec[::step]],
        }
    except Exception:
        return None


def holdout_metrics(predictions: pd.DataFrame, y_true: pd.Series) -> dict:
    """Accuracy, AUC, F1, precision, recall, specificity and the confusion
    matrix of a classifier's predictions frame (`prediction_label`, and
    `prediction_score_<label>` per class when it gives probabilities)."""
    from sklearn.metrics import (
        accuracy_score,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    pred_col = next(
        (c for c in ["Label", "prediction_label", "predicted_y"] if c in predictions.columns),
        predictions.columns[-1],
    )
    y_pred = predictions[pred_col]
    unique_classes = np.unique(y_true)
    is_binary = len(unique_classes) == 2

    # Macro averaging is defined even if a very small holdout set contains
    # only one observed class.
    metrics: dict = {
        "accuracy": safe_float(accuracy_score(y_true, y_pred)),
        "f1": safe_float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
        "precision": safe_float(precision_score(y_true, y_pred, average="macro", zero_division=0)),
        "recall": safe_float(recall_score(y_true, y_pred, average="macro", zero_division=0)),
    }

    cm = confusion_matrix(y_true, y_pred)
    metrics["confusion_matrix"] = cm.tolist()
    metrics["confusion_matrix_labels"] = sorted([str(c) for c in unique_classes])

    roc_data = pr_data = None
    if is_binary:
        tn, fp, fn, tp = cm.ravel()
        metrics["specificity"] = safe_float(tn / (tn + fp)) if (tn + fp) > 0 else 0.0

        pos_class = sorted(unique_classes)[1]
        prob_col = next(
            (
                c for c in (f"prediction_score_{pos_class}", "prediction_score")
                if c in predictions.columns
            ),
            None,
        )
        if prob_col is not None:
            y_prob = predictions[prob_col]
            try:
                metrics["auc"] = safe_float(roc_auc_score(y_true, y_prob))
                roc_data = compute_roc(y_true, y_prob, pos_label=pos_class)
                pr_data = compute_pr(y_true, y_prob, pos_label=pos_class)
            except Exception:
                pass

    return {"metrics": metrics, "roc_curve": roc_data, "pr_curve": pr_data}


def regression_metrics(predictions: pd.DataFrame, target_column: str = "target") -> Optional[dict]:
    """MSE, RMSE, MAE, R², explained variance, MAPE and residual statistics
    of a regressor's predictions, or None when there is no ground truth."""
    if target_column not in predictions.columns:
        return None
    from sklearn.metrics import (
        explained_variance_score,
        mean_absolute_error,
        mean_absolute_percentage_error,
        mean_squared_error,
        r2_score,
    )

    pred_col = next(
        (c for c in ["prediction_label", "Label", "predicted_y", "Prediction"] if c in predictions.columns),
        None,
    )
    if pred_col is None:
        return None
    y_true = predictions[target_column]
    y_pred = predictions[pred_col]
    mse = float(mean_squared_error(y_true, y_pred))
    metrics: dict = {
        "mse": mse,
        "rmse": float(np.sqrt(mse)),
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "r2": float(r2_score(y_true, y_pred)),
        "explained_variance": float(explained_variance_score(y_true, y_pred)),
    }
    nonzero = y_true != 0
    if nonzero.any():
        try:
            metrics["mape"] = float(
                mean_absolute_percentage_error(y_true[nonzero], y_pred[nonzero]) * 100
            )
        except Exception:
            pass
    residuals = y_true - y_pred
    metrics.update({
        "mean_residual": float(residuals.mean()),
        "median_residual": float(residuals.median()),
        "std_residual": float(residuals.std()) if len(residuals) > 1 else None,
    })
    return {key: _finite(value) for key, value in metrics.items()}


def chart_data_from_recorded(recorded: dict) -> dict:
    """The UI's chart payload, derived from the recorded rows."""
    comparison_table: list = []
    per_model: dict = {}
    fold = None
    for variant in recorded.get("variants", []):
        metrics = variant.get("metrics") or {}
        fold = fold or metrics.get("fold")
        if metrics.get("stage") == "comparison":
            comparison_table.append({"Model": variant["algorithm"], **(metrics.get("cv") or {})})
            continue
        evaluation = metrics.get("evaluation") or {}
        holdout = evaluation.get("metrics") or {}
        per_model[variant["variant_key"]] = {
            "label": variant["algorithm"],
            "cv_metrics": metrics.get("cv") or {},
            "holdout_metrics": holdout,
            "confusion_matrix": holdout.get("confusion_matrix"),
            "confusion_matrix_labels": holdout.get("confusion_matrix_labels"),
            "roc_curve": evaluation.get("roc_curve"),
            "pr_curve": evaluation.get("pr_curve"),
        }
    return {"comparison_table": comparison_table, "per_model": per_model, "fold": fold}


__all__ = [
    "chart_data_from_recorded",
    "compute_pr",
    "compute_roc",
    "holdout_metrics",
    "regression_metrics",
    "safe_float",
]
