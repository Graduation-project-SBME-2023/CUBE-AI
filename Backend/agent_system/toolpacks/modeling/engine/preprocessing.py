"""The preprocessing ``Pipeline`` that replaces PyCaret's ``setup()``.

Imputation (median for numbers, most frequent for categories), an optional
Yeo-Johnson transformation or standard scaling, one-hot encoding of the
categorical columns and an optional PCA, then the estimator. The settings are
explicit and recorded as they are (``model_results.preprocessing.pipeline``),
not as PyCaret setup arguments.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

TARGET = "target"


@dataclass(frozen=True)
class PreprocessingConfig:
    preprocess: bool = True  # False: no imputation, scaling or PCA (one-hot only)
    normalize: bool = False
    transformation: bool = False
    pca: bool = False
    pca_components: float | None = None
    numeric_features: tuple[str, ...] = ()
    categorical_features: tuple[str, ...] = ()
    ignore_features: tuple[str, ...] = ()
    date_features: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> PreprocessingConfig:
        """From recorded settings — the engine's own document, or the setup
        arguments a PyCaret-trained record kept (same names, lists or CSV)."""

        def names(key: str) -> tuple[str, ...]:
            value = settings.get(key)
            if not value:
                return ()
            if isinstance(value, str):
                value = value.split(",")
            return tuple(str(item).strip() for item in value if str(item).strip())

        components = settings.get("pca_components")
        return cls(
            preprocess=bool(settings.get("preprocess", True)),
            normalize=bool(settings.get("normalize", False)),
            transformation=bool(settings.get("transformation", False)),
            pca=bool(settings.get("pca", False)),
            pca_components=float(components) if components is not None else None,
            numeric_features=names("numeric_features"),
            categorical_features=names("categorical_features"),
            ignore_features=names("ignore_features"),
            date_features=names("date_features"),
        )

    def to_document(self) -> dict[str, Any]:
        document = asdict(self)
        for key in ("numeric_features", "categorical_features", "ignore_features", "date_features"):
            document[key] = list(document[key])
        return document


@dataclass(frozen=True)
class FeatureColumns:
    numeric: list[str] = field(default_factory=list)
    categorical: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)

    @property
    def used(self) -> list[str]:
        return [*self.numeric, *self.categorical]


def feature_columns(
    frame: pd.DataFrame, config: PreprocessingConfig, target: str = TARGET
) -> FeatureColumns:
    """Which columns are numeric, categorical or left out. An explicit list
    wins over the column's dtype; date and ignored columns are left out."""
    missing = [
        name
        for name in (*config.numeric_features, *config.categorical_features)
        if name not in frame.columns
    ]
    if missing:
        raise ValueError(f"feature columns not in the data: {', '.join(missing)}")
    dropped = [
        name for name in frame.columns
        if name != target and (name in config.ignore_features or name in config.date_features)
    ]
    numeric: list[str] = []
    categorical: list[str] = []
    for name in frame.columns:
        if name == target or name in dropped:
            continue
        if name in config.categorical_features:
            categorical.append(name)
        elif name in config.numeric_features:
            numeric.append(name)
        elif pd.api.types.is_bool_dtype(frame[name]) or not pd.api.types.is_numeric_dtype(frame[name]):
            categorical.append(name)
        else:
            numeric.append(name)
    if not numeric and not categorical:
        raise ValueError("no feature columns are left to train on")
    return FeatureColumns(numeric=numeric, categorical=categorical, dropped=dropped)


def build_pipeline(estimator: Any, columns: FeatureColumns, config: PreprocessingConfig) -> Any:
    """``preprocess`` (a ``ColumnTransformer``), an optional ``pca``, then ``model``."""
    from sklearn.compose import ColumnTransformer
    from sklearn.decomposition import PCA
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, PowerTransformer, StandardScaler

    encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    transformers: list[tuple[str, Any, Sequence[str]]] = []
    if columns.numeric:
        steps: list[tuple[str, Any]] = []
        if config.preprocess:
            steps.append(("impute", SimpleImputer(strategy="median")))
            if config.transformation:
                steps.append(("transform", PowerTransformer(method="yeo-johnson", standardize=True)))
            elif config.normalize:
                steps.append(("scale", StandardScaler()))
        transformers.append(("num", Pipeline(steps) if steps else "passthrough", columns.numeric))
    if columns.categorical:
        steps = [("encode", encoder)]
        if config.preprocess:
            steps.insert(0, ("impute", SimpleImputer(strategy="most_frequent")))
        transformers.append(("cat", Pipeline(steps), columns.categorical))

    stages: list[tuple[str, Any]] = [
        ("preprocess", ColumnTransformer(transformers, remainder="drop"))
    ]
    if config.preprocess and config.pca:
        stages.append(("pca", PCA(n_components=pca_components(config.pca_components))))
    stages.append(("model", estimator))
    return Pipeline(stages)


def pca_components(value: float | None) -> float | int:
    """PyCaret's convention: a ratio in (0, 1) keeps that much variance, a
    number above 1 is a component count; nothing given keeps 99 %."""
    if value is None:
        return 0.99
    return int(value) if value >= 1 else float(value)


def pipeline_document(columns: FeatureColumns, config: PreprocessingConfig) -> dict[str, Any]:
    """The explicit settings ``model_results.preprocessing`` records."""
    numeric_steps = ["passthrough"]
    if config.preprocess:
        numeric_steps = ["impute_median"]
        if config.transformation:
            numeric_steps.append("yeo_johnson")
        elif config.normalize:
            numeric_steps.append("standard_scale")
    categorical_steps = (["impute_most_frequent"] if config.preprocess else []) + ["one_hot"]
    return {
        "engine": "sklearn",
        "config": config.to_document(),
        "numeric_features": list(columns.numeric),
        "categorical_features": list(columns.categorical),
        "dropped_features": list(columns.dropped),
        "numeric_steps": numeric_steps,
        "categorical_steps": categorical_steps,
        "pca": (
            {"n_components": pca_components(config.pca_components)}
            if config.preprocess and config.pca
            else None
        ),
    }


__all__ = [
    "FeatureColumns",
    "PreprocessingConfig",
    "TARGET",
    "build_pipeline",
    "feature_columns",
    "pca_components",
    "pipeline_document",
]
