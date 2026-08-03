"""Leakage-safe multi-organ radiomics classification and regression tools."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from .. import mcp
from ..helpers.target_columns import load_feature_schema, resolve_target_column
from ..helpers.workspace import WorkspacePath, base_on_workspace_dir
from tool_logging import ProgressPayload, TextPayload, ToolEvent, log_tool_state


class InspectRadiomicsDatasetInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    input_path: WorkspacePath = Field(..., description="Fused radiomics CSV path")
    target_column: Optional[str] = Field(
        None,
        description="Patient-level outcome column; omit to discover it from schema/data",
    )
    hypothesis: Optional[str] = Field(
        None, description="Optional study hypothesis used only as a tie-breaker"
    )


class RadiomicsModelInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    input_path: WorkspacePath = Field(..., description="Fused radiomics CSV path")
    target_column: Optional[str] = Field(
        None,
        description="Patient-level outcome column; omit to discover it from schema/data",
    )
    subject_id_column: str = Field("subject_id", description="Unique patient identifier")
    group_column: Optional[str] = Field(
        None, description="Optional patient/site group used to keep groups within one fold"
    )
    fold: int = Field(5, ge=2, description="Cross-validation fold count")
    cv_repeats: int = Field(1, ge=1, description="Repeated CV count (regression only)")
    organ_prefix_delimiter: str = Field(
        "__", min_length=1, description="Delimiter between organ and feature name"
    )
    models: Optional[list[str]] = Field(
        None, description="Model IDs to include; omit to use the default suite"
    )
    missing_strategy: Literal["native_nan", "impute_with_indicator", "both"] = Field(
        "both", description="Missing-value strategies to evaluate"
    )
    correlation_threshold: float = Field(0.90, gt=0.0, le=1.0)
    generate_shap: bool = Field(True)
    session_id: int = Field(42, description="Random seed")


class CorrelationFilter:
    """Scikit-learn transformer that learns correlated columns during fit only."""

    def __init__(self, threshold: float = 0.90):
        self.threshold = threshold

    def get_params(self, deep: bool = True) -> dict[str, Any]:
        return {"threshold": self.threshold}

    def set_params(self, **params: Any) -> "CorrelationFilter":
        for key, value in params.items():
            setattr(self, key, value)
        return self

    def fit(self, X: Any, y: Any = None) -> "CorrelationFilter":
        array = np.asarray(X, dtype=float)
        if array.ndim != 2:
            raise ValueError("CorrelationFilter expects a two-dimensional feature matrix")
        self.n_features_in_ = array.shape[1]
        corr = pd.DataFrame(array).corr().abs()
        upper = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))
        self.drop_indices_ = [
            int(column)
            for column in upper.columns
            if (upper[column] > self.threshold).any()
        ]
        dropped = set(self.drop_indices_)
        self.keep_indices_ = np.asarray(
            [index for index in range(self.n_features_in_) if index not in dropped],
            dtype=int,
        )
        if not len(self.keep_indices_):
            raise ValueError("Correlation filtering removed every feature")
        return self

    def transform(self, X: Any) -> np.ndarray:
        array = np.asarray(X, dtype=float)
        return array[:, self.keep_indices_]

    def get_feature_names_out(self, input_features: Any = None) -> np.ndarray:
        if input_features is None:
            input_features = np.asarray(
                [f"x{index}" for index in range(self.n_features_in_)], dtype=object
            )
        return np.asarray(input_features, dtype=object)[self.keep_indices_]


@dataclass(frozen=True)
class ModelSpec:
    estimator: Any
    supports_nan: bool
    scale: bool


def detect_task_type(target: pd.Series, hypothesis: Optional[str] = None) -> dict[str, Any]:
    """Infer classification versus regression, with target values taking precedence."""
    clean = target.dropna()
    if clean.empty:
        raise ValueError("Target column contains no usable values")

    unique_count = int(clean.nunique())
    unique_ratio = float(unique_count / len(clean))
    numeric = pd.to_numeric(clean, errors="coerce")
    is_numeric = bool(numeric.notna().all())

    if not is_numeric:
        task_type = "classification"
        reason = "The target contains non-numeric categories."
    else:
        integer_like = bool(np.allclose(numeric, np.round(numeric)))
        categorical_limit = max(10, min(20, int(np.sqrt(len(clean)) * 2)))
        if unique_count <= categorical_limit and (integer_like or unique_ratio <= 0.20):
            task_type = "classification"
            reason = f"The numeric target has {unique_count} discrete levels."
        else:
            task_type = "regression"
            reason = f"The numeric target has {unique_count} values across {len(clean)} rows."

    hypothesis_hint = None
    if hypothesis:
        lowered = hypothesis.casefold()
        regression_terms = ("predict value", "continuous", "volume", "size", "score", "age")
        classification_terms = ("classify", "diagnosis", "positive", "negative", "presence")
        if any(term in lowered for term in regression_terms):
            hypothesis_hint = "regression"
        elif any(term in lowered for term in classification_terms):
            hypothesis_hint = "classification"

    return {
        "task_type": task_type,
        "reason": reason,
        "hypothesis_hint": hypothesis_hint,
        "hypothesis_conflicts_with_data": bool(
            hypothesis_hint and hypothesis_hint != task_type
        ),
        "rows": int(len(clean)),
        "unique_target_values": unique_count,
        "unique_ratio": round(unique_ratio, 6),
        "missing_target_values": int(target.isna().sum()),
    }


def parse_organ_feature_sets(
    columns: list[str], delimiter: str = "__"
) -> tuple[list[str], list[str], dict[str, list[str]]]:
    radiomic_columns = sorted(column for column in columns if delimiter in column)
    presence_columns = sorted(column for column in columns if column.endswith("_present"))
    organs = sorted(
        {column.split(delimiter, 1)[0] for column in radiomic_columns}
        | {column[: -len("_present")] for column in presence_columns}
    )
    by_organ: dict[str, list[str]] = {}
    for organ in organs:
        organ_columns = [
            column
            for column in radiomic_columns
            if column.startswith(f"{organ}{delimiter}")
        ]
        present = f"{organ}_present"
        if present in presence_columns:
            organ_columns.append(present)
        if organ_columns:
            by_organ[organ] = organ_columns
    return radiomic_columns, presence_columns, by_organ


def build_comparison_datasets(
    columns: list[str], delimiter: str = "__"
) -> dict[str, list[str]]:
    radiomic, presence, by_organ = parse_organ_feature_sets(columns, delimiter)
    if not radiomic:
        raise ValueError(
            f"No radiomic columns use the expected '<organ>{delimiter}<feature>' convention"
        )
    datasets = {
        f"baseline:{organ}": feature_columns
        for organ, feature_columns in by_organ.items()
        if any(delimiter in column for column in feature_columns)
    }
    datasets["fusion:radiomics_only"] = radiomic
    datasets["fusion:radiomics_plus_presence"] = radiomic + presence
    if presence:
        datasets["presence_only"] = presence
    return datasets


def _load_modeling_data(
    params: RadiomicsModelInput,
) -> tuple[pd.DataFrame, pd.Series, dict[str, list[str]], str, str]:
    if not params.input_path or not os.path.isfile(params.input_path):
        raise FileNotFoundError(f"Input CSV not found: {params.input_path}")
    data = pd.read_csv(params.input_path)
    target_column, target_resolution = resolve_target_column(
        data.columns,
        requested=params.target_column,
        schema=load_feature_schema(params.input_path),
        feature_delimiter=params.organ_prefix_delimiter,
    )
    required = [params.subject_id_column, target_column]
    missing = [column for column in required if column not in data.columns]
    if missing:
        raise ValueError(f"Missing required column(s): {missing}")
    if data.empty:
        raise ValueError("Input CSV is empty")
    if data[params.subject_id_column].isna().any():
        raise ValueError("Subject IDs cannot be missing")
    if data[target_column].isna().any():
        raise ValueError("Target values cannot be missing")
    if params.group_column and params.group_column not in data.columns:
        raise ValueError(f"Group column not found: {params.group_column}")

    ignored = {target_column, params.subject_id_column, params.group_column}
    feature_candidates = [column for column in data.columns if column not in ignored]
    datasets = build_comparison_datasets(
        feature_candidates, params.organ_prefix_delimiter
    )
    feature_columns = sorted({column for values in datasets.values() for column in values})
    converted = data[feature_columns].apply(pd.to_numeric, errors="coerce")
    newly_missing = converted.isna() & data[feature_columns].notna()
    invalid = sorted(column for column in feature_columns if newly_missing[column].any())
    if invalid:
        raise ValueError(f"Radiomic features must be numeric; invalid columns: {invalid}")
    data.loc[:, feature_columns] = converted
    return data, data[target_column], datasets, target_column, target_resolution


def _classification_models(seed: int) -> dict[str, ModelSpec]:
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression

    return {
        "hgb": ModelSpec(
            HistGradientBoostingClassifier(random_state=seed), True, False
        ),
        "rf": ModelSpec(
            RandomForestClassifier(
                n_estimators=300,
                class_weight="balanced",
                n_jobs=-1,
                random_state=seed,
            ),
            False,
            False,
        ),
        "lr_l1": ModelSpec(
            LogisticRegression(
                penalty="l1",
                solver="saga",
                class_weight="balanced",
                max_iter=5000,
                random_state=seed,
            ),
            False,
            True,
        ),
        "lr_elastic_net": ModelSpec(
            LogisticRegression(
                penalty="elasticnet",
                solver="saga",
                l1_ratio=0.5,
                class_weight="balanced",
                max_iter=5000,
                random_state=seed,
            ),
            False,
            True,
        ),
    }


def _regression_models(seed: int) -> dict[str, ModelSpec]:
    from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
    from sklearn.linear_model import LassoCV, LinearRegression, RidgeCV
    from sklearn.neighbors import KNeighborsRegressor
    from sklearn.svm import SVR
    from sklearn.tree import DecisionTreeRegressor

    return {
        "linear": ModelSpec(LinearRegression(), False, True),
        "ridge": ModelSpec(
            RidgeCV(alphas=np.logspace(-4, 4, 25)), False, True
        ),
        "lasso": ModelSpec(
            LassoCV(cv=3, max_iter=10000, random_state=seed), False, True
        ),
        "knn": ModelSpec(KNeighborsRegressor(n_neighbors=3), False, True),
        "svr": ModelSpec(SVR(kernel="rbf"), False, True),
        "decision_tree": ModelSpec(
            DecisionTreeRegressor(random_state=seed), False, False
        ),
        "random_forest": ModelSpec(
            RandomForestRegressor(n_estimators=300, n_jobs=-1, random_state=seed),
            False,
            False,
        ),
        "gradient_boosting": ModelSpec(
            GradientBoostingRegressor(random_state=seed), False, False
        ),
    }


def _select_models(
    available: dict[str, ModelSpec], requested: Optional[list[str]]
) -> dict[str, ModelSpec]:
    if not requested:
        return available
    unknown = sorted(set(requested) - set(available))
    if unknown:
        raise ValueError(
            f"Unknown model ID(s): {unknown}. Available IDs: {sorted(available)}"
        )
    return {name: available[name] for name in requested}


def _pipeline_variants(
    spec: ModelSpec, params: RadiomicsModelInput
) -> list[tuple[str, Any]]:
    from sklearn.base import clone
    from sklearn.feature_selection import VarianceThreshold
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    strategies: list[str]
    if params.missing_strategy == "both":
        strategies = ["native_nan", "impute_with_indicator"] if spec.supports_nan else ["impute_with_indicator"]
    elif params.missing_strategy == "native_nan" and not spec.supports_nan:
        strategies = ["impute_with_indicator"]
    else:
        strategies = [params.missing_strategy]

    variants = []
    for strategy in strategies:
        steps: list[tuple[str, Any]] = []
        if strategy == "impute_with_indicator":
            steps.append(
                (
                    "imputer",
                    SimpleImputer(
                        strategy="median", add_indicator=True, keep_empty_features=True
                    ),
                )
            )
        steps.extend(
            [
                ("variance", VarianceThreshold(0.0)),
                ("correlation", CorrelationFilter(params.correlation_threshold)),
            ]
        )
        if spec.scale:
            steps.append(("scaler", StandardScaler()))
        steps.append(("estimator", clone(spec.estimator)))
        variants.append((strategy, Pipeline(steps)))
    return variants


def _classification_splits(
    data: pd.DataFrame, y: pd.Series, params: RadiomicsModelInput
) -> tuple[list[tuple[np.ndarray, np.ndarray]], int]:
    from sklearn.model_selection import StratifiedGroupKFold, StratifiedKFold

    minority = int(y.value_counts().min())
    effective_fold = min(params.fold, minority)
    if effective_fold < 2:
        raise ValueError("Each class needs at least two samples for cross-validation")
    if params.group_column:
        groups = data[params.group_column]
        effective_fold = min(effective_fold, int(groups.nunique()))
        if effective_fold < 2:
            raise ValueError("At least two distinct groups are required")
        splitter = StratifiedGroupKFold(
            n_splits=effective_fold, shuffle=True, random_state=params.session_id
        )
        return list(splitter.split(data, y, groups)), effective_fold
    splitter = StratifiedKFold(
        n_splits=effective_fold, shuffle=True, random_state=params.session_id
    )
    return list(splitter.split(data, y)), effective_fold


def _regression_splits(
    data: pd.DataFrame, y: pd.Series, params: RadiomicsModelInput
) -> tuple[list[tuple[np.ndarray, np.ndarray]], int]:
    from sklearn.model_selection import GroupKFold, KFold, RepeatedKFold

    effective_fold = min(params.fold, len(data))
    if params.group_column:
        groups = data[params.group_column]
        effective_fold = min(effective_fold, int(groups.nunique()))
        if effective_fold < 2:
            raise ValueError("At least two distinct groups are required")
        splitter = GroupKFold(n_splits=effective_fold)
        return list(splitter.split(data, y, groups)), effective_fold
    if effective_fold < 2:
        raise ValueError("At least two samples are required")
    if params.cv_repeats > 1:
        splitter = RepeatedKFold(
            n_splits=effective_fold,
            n_repeats=params.cv_repeats,
            random_state=params.session_id,
        )
    else:
        splitter = KFold(
            n_splits=effective_fold, shuffle=True, random_state=params.session_id
        )
    return list(splitter.split(data, y)), effective_fold


def _classification_metrics(y_true: pd.Series, y_pred: np.ndarray, proba: Any) -> dict[str, float]:
    from sklearn.metrics import (
        accuracy_score,
        balanced_accuracy_score,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    classes = np.asarray(sorted(pd.unique(y_true)))
    binary = len(classes) == 2
    average = "binary" if binary else "macro"
    kwargs = {"pos_label": classes[-1]} if binary else {"average": average}
    metrics = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)),
        "sensitivity": float(recall_score(y_true, y_pred, zero_division=0, **kwargs)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0, **kwargs)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0, **kwargs)),
    }
    cm = confusion_matrix(y_true, y_pred, labels=classes)
    if binary:
        tn, fp, fn, tp = cm.ravel()
        metrics["specificity"] = float(tn / (tn + fp)) if tn + fp else 0.0
        if proba is not None and len(pd.unique(y_true)) == 2:
            metrics["auc"] = float(roc_auc_score(y_true, proba[:, -1]))
    else:
        specificities = []
        total = cm.sum()
        for index in range(len(classes)):
            tp = cm[index, index]
            fp = cm[:, index].sum() - tp
            fn = cm[index, :].sum() - tp
            tn = total - tp - fp - fn
            specificities.append(tn / (tn + fp) if tn + fp else 0.0)
        metrics["specificity"] = float(np.mean(specificities))
        if proba is not None and len(pd.unique(y_true)) == len(classes):
            metrics["auc"] = float(
                roc_auc_score(y_true, proba, labels=classes, multi_class="ovr", average="macro")
            )
    return metrics


def _regression_metrics(y_true: pd.Series, y_pred: np.ndarray) -> dict[str, float]:
    from sklearn.metrics import (
        explained_variance_score,
        mean_absolute_error,
        mean_squared_error,
        median_absolute_error,
        r2_score,
    )

    actual = np.asarray(y_true, dtype=float)
    predicted = np.asarray(y_pred, dtype=float)
    has_variation = (
        len(actual) > 1
        and not np.isclose(np.std(actual), 0.0)
        and not np.isclose(np.std(predicted), 0.0)
    )
    pearson = np.corrcoef(actual, predicted)[0, 1] if has_variation else np.nan
    rank_actual = pd.Series(actual).rank().to_numpy()
    rank_predicted = pd.Series(predicted).rank().to_numpy()
    has_rank_variation = (
        len(actual) > 1
        and not np.isclose(np.std(rank_actual), 0.0)
        and not np.isclose(np.std(rank_predicted), 0.0)
    )
    spearman = (
        np.corrcoef(rank_actual, rank_predicted)[0, 1]
        if has_rank_variation
        else np.nan
    )
    return {
        "mae": float(mean_absolute_error(actual, predicted)),
        "mse": float(mean_squared_error(actual, predicted)),
        "rmse": float(np.sqrt(mean_squared_error(actual, predicted))),
        "r2": float(r2_score(actual, predicted)),
        "median_absolute_error": float(median_absolute_error(actual, predicted)),
        "explained_variance": float(explained_variance_score(actual, predicted)),
        "pearson": float(pearson),
        "spearman": float(spearman),
    }


def _aggregate_metrics(fold_metrics: pd.DataFrame) -> pd.DataFrame:
    metric_columns = [
        column
        for column in fold_metrics.select_dtypes(include=[np.number]).columns
        if column != "fold"
    ]
    aggregated = (
        fold_metrics.groupby(["dataset", "model", "missing_strategy"])[metric_columns]
        .agg(["mean", "std"])
        .reset_index()
    )
    aggregated.columns = [
        column if isinstance(column, str) else "_".join(part for part in column if part)
        for column in aggregated.columns
    ]
    return aggregated


_REGRESSION_MODEL_LABELS = {
    "linear": "Linear Regression (OLS)",
    "ridge": "Ridge Regression",
    "lasso": "Lasso Regression",
    "knn": "K-Nearest Neighbors",
    "svr": "Support Vector Regression",
    "decision_tree": "Decision Tree Regressor",
    "random_forest": "Random Forest Regressor",
    "gradient_boosting": "Gradient Boosting Regressor",
}


def _finite_float(value: Any) -> Optional[float]:
    """Convert numpy/pandas numeric values to JSON-safe finite floats."""
    try:
        converted = float(value)
        return converted if np.isfinite(converted) else None
    except (TypeError, ValueError):
        return None


def _regression_chart_data(
    comparison: pd.DataFrame,
    candidates: list[dict[str, Any]],
    y: pd.Series,
    effective_fold: int,
) -> dict[str, Any]:
    """Build the frontend contract for regression comparison and diagnostics."""
    metric_columns = {
        "MAE": "mae_mean",
        "MSE": "mse_mean",
        "RMSE": "rmse_mean",
        "R2": "r2_mean",
        "MedianAE": "median_absolute_error_mean",
        "ExplainedVariance": "explained_variance_mean",
        "Pearson": "pearson_mean",
    }
    comparison_rows = []
    ordered = comparison.sort_values("mae_mean", ascending=True, na_position="last")
    for _, row in ordered.iterrows():
        record: dict[str, Any] = {
            "Model": _REGRESSION_MODEL_LABELS.get(str(row["model"]), str(row["model"])),
            "Dataset": str(row["dataset"]),
            "Missing Strategy": str(row["missing_strategy"]),
        }
        for label, column in metric_columns.items():
            record[label] = _finite_float(row.get(column))
        comparison_rows.append(record)

    per_model: dict[str, Any] = {}
    actual = np.asarray(y, dtype=float)
    for candidate in candidates:
        predicted = np.asarray(candidate["oof_prediction"], dtype=float)
        metrics = _regression_metrics(y, predicted)
        dataset = str(candidate["dataset"])
        model_id = str(candidate["model"])
        strategy = str(candidate["missing_strategy"])
        key = f"{dataset}|{model_id}|{strategy}"
        label = _REGRESSION_MODEL_LABELS.get(model_id, model_id)

        indices = np.arange(len(actual))
        if len(indices) > 500:
            indices = np.linspace(0, len(indices) - 1, 500, dtype=int)
        points = [
            {
                "actual": round(float(actual[index]), 6),
                "predicted": round(float(predicted[index]), 6),
                "residual": round(float(actual[index] - predicted[index]), 6),
            }
            for index in indices
        ]
        per_model[key] = {
            "label": label,
            "model_id": model_id,
            "dataset": dataset,
            "missing_strategy": strategy,
            "cv_metrics": {
                "MAE": _finite_float(metrics["mae"]),
                "MSE": _finite_float(metrics["mse"]),
                "RMSE": _finite_float(metrics["rmse"]),
                "R2": _finite_float(metrics["r2"]),
                "MedianAE": _finite_float(metrics["median_absolute_error"]),
                "ExplainedVariance": _finite_float(metrics["explained_variance"]),
                "Pearson": _finite_float(metrics["pearson"]),
            },
            "holdout_metrics": {},
            "prediction_points": points,
            "confusion_matrix": None,
            "confusion_matrix_labels": None,
            "roc_curve": None,
            "pr_curve": None,
        }
    return {
        "task_type": "regression",
        "comparison_table": comparison_rows,
        "per_model": per_model,
        "fold": effective_fold,
        "metric_definitions": {
            "MAE": "Mean Absolute Error (lower is better)",
            "MSE": "Mean Squared Error (lower is better)",
            "RMSE": "Root Mean Squared Error (lower is better)",
            "R2": "Coefficient of Determination (higher is better)",
            "MedianAE": "Median Absolute Error (lower is better)",
            "ExplainedVariance": "Explained Variance (higher is better)",
            "Pearson": "Pearson Correlation (higher is better)",
        },
    }


def _organ_for_feature(feature: str, delimiter: str) -> str:
    cleaned = feature.replace("missingindicator_", "", 1)
    if cleaned.endswith("_present"):
        return cleaned[: -len("_present")]
    if delimiter in cleaned:
        return cleaned.split(delimiter, 1)[0]
    return "other"


def _write_explainability(
    pipeline: Any,
    X: pd.DataFrame,
    y: pd.Series,
    output_dir: str,
    delimiter: str,
    task_type: str,
    generate_shap: bool,
    seed: int,
) -> tuple[Optional[str], str, list[str]]:
    from sklearn.inspection import permutation_importance

    warnings: list[str] = []
    sample = X.sample(min(len(X), 500), random_state=seed) if len(X) > 500 else X
    sample_y = y.loc[sample.index]
    scoring = "roc_auc" if task_type == "classification" and y.nunique() == 2 else (
        "balanced_accuracy" if task_type == "classification" else "neg_mean_absolute_error"
    )
    permutation = permutation_importance(
        pipeline, sample, sample_y, scoring=scoring, n_repeats=10, random_state=seed, n_jobs=-1
    )
    feature_importance = pd.DataFrame(
        {"feature": X.columns, "importance": permutation.importances_mean}
    )
    feature_importance["organ"] = feature_importance["feature"].map(
        lambda value: _organ_for_feature(str(value), delimiter)
    )
    organ_importance = (
        feature_importance.groupby("organ", as_index=False)["importance"]
        .sum()
        .sort_values("importance", ascending=False)
    )
    organ_importance_path = os.path.join(output_dir, "organ_importance.csv")
    organ_importance.to_csv(organ_importance_path, index=False)

    shap_path = None
    if generate_shap:
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import shap

            transformed: Any = sample
            names = np.asarray(sample.columns, dtype=object)
            for _, transformer in pipeline.steps[:-1]:
                transformed = transformer.transform(transformed)
                if hasattr(transformer, "get_feature_names_out"):
                    names = transformer.get_feature_names_out(names)
            estimator = pipeline.steps[-1][1]
            explainer = shap.Explainer(estimator, transformed)
            shap_values = explainer(transformed)
            shap.summary_plot(shap_values, transformed, feature_names=names, show=False)
            shap_path = os.path.join(output_dir, "shap_summary.png")
            plt.savefig(shap_path, dpi=160, bbox_inches="tight")
            plt.close()
        except Exception as exc:
            warnings.append(f"SHAP generation skipped: {exc}")
    return shap_path, organ_importance_path, warnings


def _write_regression_plots(oof: pd.DataFrame, output_dir: str) -> tuple[Optional[str], Optional[str]]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        actual = oof["actual"]
        predicted = oof["oof_prediction"]
        predicted_path = os.path.join(output_dir, "predicted_vs_actual.png")
        residual_path = os.path.join(output_dir, "residual_plot.png")

        plt.figure(figsize=(6, 5))
        plt.scatter(actual, predicted, alpha=0.7)
        low, high = min(actual.min(), predicted.min()), max(actual.max(), predicted.max())
        plt.plot([low, high], [low, high], linestyle="--", color="black")
        plt.xlabel("Actual")
        plt.ylabel("Predicted")
        plt.tight_layout()
        plt.savefig(predicted_path, dpi=160)
        plt.close()

        plt.figure(figsize=(6, 5))
        plt.scatter(predicted, actual - predicted, alpha=0.7)
        plt.axhline(0, linestyle="--", color="black")
        plt.xlabel("Predicted")
        plt.ylabel("Residual")
        plt.tight_layout()
        plt.savefig(residual_path, dpi=160)
        plt.close()
        return predicted_path, residual_path
    except Exception:
        return None, None


def _train_radiomics(params: RadiomicsModelInput, task_type: str) -> dict[str, Any]:
    from sklearn.base import clone
    import joblib

    data, y, datasets, target_column, target_resolution = _load_modeling_data(params)
    if task_type == "classification":
        if y.nunique() < 2:
            raise ValueError("Classification target needs at least two classes")
        splits, effective_fold = _classification_splits(data, y, params)
        models = _select_models(_classification_models(params.session_id), params.models)
    else:
        y = pd.to_numeric(y, errors="raise")
        splits, effective_fold = _regression_splits(data, y, params)
        models = _select_models(_regression_models(params.session_id), params.models)

    run_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    output_dir = base_on_workspace_dir(f"modeling/{task_type}/radiomics_{run_id}")
    os.makedirs(output_dir, exist_ok=True)
    log_file = os.path.join(output_dir, "training.log")
    logger = logging.getLogger(f"{__name__}.{task_type}")
    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    candidates = []
    for dataset_name, columns in datasets.items():
        for model_name, spec in models.items():
            for strategy, pipeline in _pipeline_variants(spec, params):
                candidates.append((dataset_name, columns, model_name, strategy, pipeline))
    total_fits = max(1, len(candidates) * len(splits))
    completed_fits = 0
    fold_rows: list[dict[str, Any]] = []
    failures: list[str] = []
    completed_candidates: list[dict[str, Any]] = []
    best: Optional[dict[str, Any]] = None
    best_fusion: Optional[dict[str, Any]] = None

    log_tool_state(
        ToolEvent(
            "tool_started",
            "text",
            TextPayload(message=f"Starting {task_type} radiomics comparison"),
        )
    )

    for dataset_name, columns, model_name, strategy, pipeline in candidates:
        oof_prediction = np.empty(
            len(data), dtype=object if task_type == "classification" else float
        )
        oof_prediction_sum = np.zeros(len(data), dtype=float)
        oof_prediction_count = np.zeros(len(data), dtype=int)
        oof_probability = None
        if task_type == "classification" and y.nunique() == 2:
            oof_probability = np.full(len(data), np.nan, dtype=float)
        scores: list[float] = []
        candidate_failed = False
        for fold_index, (train_index, test_index) in enumerate(splits):
            completed_fits += 1
            log_tool_state(
                ToolEvent(
                    "tool_progress",
                    "progress",
                    ProgressPayload(
                        current=completed_fits,
                        total=total_fits,
                        label=f"{dataset_name} / {model_name} / {strategy}",
                    ),
                )
            )
            fitted = clone(pipeline)
            try:
                fitted.fit(data.iloc[train_index][columns], y.iloc[train_index])
                prediction = fitted.predict(data.iloc[test_index][columns])
                probability = fitted.predict_proba(data.iloc[test_index][columns]) if (
                    task_type == "classification" and hasattr(fitted, "predict_proba")
                ) else None
                if task_type == "classification":
                    oof_prediction[test_index] = prediction
                else:
                    oof_prediction_sum[test_index] += prediction
                    oof_prediction_count[test_index] += 1
                if oof_probability is not None and probability is not None:
                    oof_probability[test_index] = probability[:, -1]
                if task_type == "classification":
                    metrics = _classification_metrics(y.iloc[test_index], prediction, probability)
                    score = metrics.get("auc", metrics["balanced_accuracy"])
                else:
                    metrics = _regression_metrics(y.iloc[test_index], prediction)
                    score = -metrics["mae"]
                fold_rows.append(
                    {
                        "dataset": dataset_name,
                        "model": model_name,
                        "missing_strategy": strategy,
                        "fold": fold_index,
                        "selection_score": score,
                        **metrics,
                    }
                )
                scores.append(float(score))
            except Exception as exc:
                message = f"{dataset_name}/{model_name}/{strategy} failed on fold {fold_index}: {exc}"
                logger.warning(message)
                failures.append(message)
                candidate_failed = True
                break
        if candidate_failed or not scores:
            continue
        if task_type == "regression":
            if (oof_prediction_count == 0).any():
                failures.append(
                    f"{dataset_name}/{model_name}/{strategy} did not predict every row"
                )
                continue
            oof_prediction = oof_prediction_sum / oof_prediction_count
        candidate = {
            "dataset": dataset_name,
            "columns": columns,
            "model": model_name,
            "missing_strategy": strategy,
            "pipeline": pipeline,
            "score": float(np.mean(scores)),
            "oof_prediction": oof_prediction,
            "oof_probability": oof_probability,
        }
        completed_candidates.append(candidate)
        if best is None or candidate["score"] > best["score"]:
            best = candidate
        if dataset_name.startswith("fusion:") and (
            best_fusion is None or candidate["score"] > best_fusion["score"]
        ):
            best_fusion = candidate

    if best is None:
        raise RuntimeError("Every candidate model failed. See training.log for details")
    fold_metrics = pd.DataFrame(fold_rows)
    fold_metrics_path = os.path.join(output_dir, "fold_metrics.csv")
    fold_metrics.to_csv(fold_metrics_path, index=False)
    comparison = _aggregate_metrics(fold_metrics)
    comparison_path = os.path.join(output_dir, "baseline_vs_fusion_metrics.csv")
    comparison.to_csv(comparison_path, index=False)

    if task_type == "classification":
        comparison_metric = "selection_score_mean"
        best_rows = comparison.loc[
            comparison.groupby("dataset")[comparison_metric].idxmax()
        ]
    else:
        comparison_metric = "mae_mean"
        best_rows = comparison.loc[
            comparison.groupby("dataset")[comparison_metric].idxmin()
        ]
    fusion_rows = best_rows[best_rows["dataset"].str.startswith("fusion:")]
    fusion_gain_path = None
    if not fusion_rows.empty:
        best_fusion_row = (
            fusion_rows.loc[fusion_rows[comparison_metric].idxmax()]
            if task_type == "classification"
            else fusion_rows.loc[fusion_rows[comparison_metric].idxmin()]
        )
        gain_rows = []
        for _, baseline_row in best_rows[
            best_rows["dataset"].str.startswith("baseline:")
        ].iterrows():
            baseline_value = float(baseline_row[comparison_metric])
            fusion_value = float(best_fusion_row[comparison_metric])
            gain = (
                fusion_value - baseline_value
                if task_type == "classification"
                else baseline_value - fusion_value
            )
            gain_rows.append(
                {
                    "baseline_dataset": baseline_row["dataset"],
                    "baseline_model": baseline_row["model"],
                    "fusion_dataset": best_fusion_row["dataset"],
                    "fusion_model": best_fusion_row["model"],
                    "metric": comparison_metric,
                    "baseline_value": baseline_value,
                    "fusion_value": fusion_value,
                    "fusion_gain_positive_is_better": gain,
                }
            )
        fusion_gain_path = os.path.join(output_dir, "fusion_gain.csv")
        pd.DataFrame(gain_rows).to_csv(fusion_gain_path, index=False)

    fitted_best = clone(best["pipeline"]).fit(data[best["columns"]], y)
    model_path = os.path.join(output_dir, "best_model.joblib")
    joblib.dump(
        {
            "pipeline": fitted_best,
            "feature_columns": best["columns"],
            "task_type": task_type,
            "target_column": target_column,
            "target_resolution": target_resolution,
            "dataset": best["dataset"],
            "model": best["model"],
            "missing_strategy": best["missing_strategy"],
        },
        model_path,
    )

    oof = data[[params.subject_id_column]].copy()
    oof["actual"] = y
    oof["oof_prediction"] = best["oof_prediction"]
    if best["oof_probability"] is not None:
        oof["oof_probability"] = best["oof_probability"]
    oof_path = os.path.join(output_dir, "oof_predictions.csv")
    oof.to_csv(oof_path, index=False)
    residuals_path = None
    predicted_plot = residual_plot = None
    if task_type == "regression":
        oof["residual"] = oof["actual"] - oof["oof_prediction"].astype(float)
        residuals_path = os.path.join(output_dir, "residuals.csv")
        oof.to_csv(residuals_path, index=False)
        predicted_plot, residual_plot = _write_regression_plots(oof, output_dir)

    shap_path = organ_importance_path = fusion_model_path = None
    explanation_warnings: list[str] = []
    if best_fusion is not None:
        fitted_fusion = clone(best_fusion["pipeline"]).fit(
            data[best_fusion["columns"]], y
        )
        fusion_model_path = os.path.join(output_dir, "best_fusion_model.joblib")
        joblib.dump(
            {
                "pipeline": fitted_fusion,
                "feature_columns": best_fusion["columns"],
                "task_type": task_type,
                "dataset": best_fusion["dataset"],
                "model": best_fusion["model"],
                "missing_strategy": best_fusion["missing_strategy"],
            },
            fusion_model_path,
        )
        try:
            shap_path, organ_importance_path, explanation_warnings = _write_explainability(
                fitted_fusion,
                data[best_fusion["columns"]],
                y,
                output_dir,
                params.organ_prefix_delimiter,
                task_type,
                params.generate_shap,
                params.session_id,
            )
        except Exception as exc:
            explanation_warnings.append(f"Explainability generation skipped: {exc}")

    log_tool_state(
        ToolEvent(
            "tool_finished",
            "text",
            TextPayload(message=f"Completed {task_type} radiomics comparison"),
        )
    )
    primary_metric = (
        "auc_with_balanced_accuracy_fallback"
        if task_type == "classification"
        else "negative_mae"
    )
    result = {
        "status": "success",
        "experiment_name": f"radiomics_{task_type}_{run_id}",
        "task_type": task_type,
        "target_column": target_column,
        "target_resolution": target_resolution,
        "effective_fold": effective_fold,
        "cv_repeats": params.cv_repeats if task_type == "regression" else 1,
        "organs": sorted(
            name.split(":", 1)[1]
            for name in datasets
            if name.startswith("baseline:")
        ),
        "best_model": best["model"],
        "best_dataset": best["dataset"],
        "best_missing_strategy": best["missing_strategy"],
        "selection_metric": primary_metric,
        "best_score": best["score"],
        "output_dir": output_dir,
        "baseline_vs_fusion_metrics": comparison_path,
        "fusion_gain": fusion_gain_path,
        "fold_metrics": fold_metrics_path,
        "oof_predictions": oof_path,
        "residuals": residuals_path,
        "best_model_path": model_path,
        "best_fusion_model_path": fusion_model_path,
        "predicted_vs_actual_plot": predicted_plot,
        "residual_plot": residual_plot,
        "shap_summary_path": shap_path,
        "organ_importance_path": organ_importance_path,
        "warnings": failures + explanation_warnings,
        "log_file": log_file,
    }
    if task_type == "regression":
        result["chart_data"] = _regression_chart_data(
            comparison, completed_candidates, y, effective_fold
        )
    return result


@mcp.tool(
    name="radiomics_inspect_dataset",
    annotations={
        "title": "Inspect a Radiomics Dataset and Infer Task Type",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def radiomics_inspect_dataset(params: InspectRadiomicsDatasetInput) -> str:
    try:
        if not params.input_path or not os.path.isfile(params.input_path):
            raise FileNotFoundError(f"Input CSV not found: {params.input_path}")
        data = pd.read_csv(params.input_path)
        target_column, target_resolution = resolve_target_column(
            data.columns,
            requested=params.target_column,
            schema=load_feature_schema(params.input_path),
        )
        result = detect_task_type(data[target_column], params.hypothesis)
        radiomic, presence, organs = parse_organ_feature_sets(data.columns.tolist())
        return json.dumps(
            {
                "status": "success",
                "target_column": target_column,
                "requested_target_column": params.target_column,
                "target_resolution": target_resolution,
                **result,
                "organs": sorted(organs),
                "radiomic_feature_count": len(radiomic),
                "presence_indicator_count": len(presence),
            },
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"status": "error", "error_message": str(exc)}, indent=2)


@mcp.tool(
    name="radiomics_train_classifier",
    annotations={
        "title": "Compare Organ Baselines with Multi-Organ Classification",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def radiomics_train_classifier(params: RadiomicsModelInput) -> str:
    try:
        return json.dumps(_train_radiomics(params, "classification"), indent=2)
    except Exception as exc:
        return json.dumps(
            {"status": "error", "error_message": str(exc), "input_path": params.input_path},
            indent=2,
        )


@mcp.tool(
    name="radiomics_train_regressor",
    annotations={
        "title": "Compare Organ Baselines with Multi-Organ Regression",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def radiomics_train_regressor(params: RadiomicsModelInput) -> str:
    try:
        return json.dumps(_train_radiomics(params, "regression"), indent=2)
    except Exception as exc:
        return json.dumps(
            {"status": "error", "error_message": str(exc), "input_path": params.input_path},
            indent=2,
        )
