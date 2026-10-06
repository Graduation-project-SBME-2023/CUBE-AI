"""Regression tools on the scikit-learn engine: ``train_regressor`` and
``run_regression_inference`` (merged plan, Phase 7.1; formerly the PyCaret
tools)."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .._toolkit import tool
from ..common.workspace import WorkspacePath
from . import inference, manifest, results, training
from .engine.estimators import REGRESSION


# ── Input Models ───────────────────────────────────────────────────────────────

class TrainRegressorInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    feature_set_id: Optional[str] = Field(
        default=None,
        description=(
            "The frozen feature set to train on — the normal case. Its label is joined "
            "from the cohort, and evaluation runs on exactly the rows feature selection "
            "withheld."
        ),
    )
    input_path: WorkspacePath = Field(
        default=None,
        description=(
            "A generic CSV to train on instead of a feature set. Its exact bytes are "
            "captured as an input artifact so the result stays reproducible."
        ),
    )
    holdout_path: Optional[str] = Field(
        default=None,
        description=(
            "With input_path only: a CSV of rows to evaluate on instead of a random "
            "split. Auto-detected from a sibling '<input>_holdout.csv' when omitted. A "
            "feature set carries its own holdout."
        ),
    )
    experiment_name: Optional[str] = Field(
        default=None,
        description="Display label. The comparison itself is identified by its input and configuration.",
    )
    fold: int = Field(
        default=10, description="Number of cross-validation folds."
    )
    session_id: Optional[int] = Field(
        default=None,
        description=(
            "Random seed. Derived from the input's digest when omitted, so a replay "
            "trains with the same seed."
        ),
    )
    use_gpu: bool = Field(
        default=False, description="Accepted for compatibility; the scikit-learn engine trains on the CPU."
    )
    data_split_stratify: bool = Field(
        default=False,
        description="Use stratified train/test splitting. Usually False for regression.",
    )
    data_split_shuffle: bool = Field(
        default=True, description="Shuffle data before splitting."
    )
    preprocess: bool = Field(
        default=True, description="Impute missing values (and scale / transform / PCA when asked)."
    )
    ignore_features: Optional[str] = Field(
        default=None, description="Comma-separated column names to exclude from training."
    )
    numeric_features: Optional[str] = Field(
        default=None, description="Comma-separated list of columns to treat as numeric."
    )
    categorical_features: Optional[str] = Field(
        default=None, description="Comma-separated list of columns to treat as categorical."
    )
    date_features: Optional[str] = Field(
        default=None, description="Comma-separated date columns (left out)."
    )
    n_select: int = Field(
        default=3, description="Number of top models to select for tuning and blending."
    )
    normalize: bool = Field(
        default=True,
        description="Normalize numeric features before training. Usually True for regression.",
    )
    transformation: bool = Field(
        default=True,
        description="Yeo-Johnson transform the numeric features. Usually True for regression.",
    )
    pca: bool = Field(
        default=False, description="Reduce dimensionality with PCA."
    )
    pca_components: Optional[float] = Field(
        default=None,
        description="PCA components: float 0–1 for variance ratio, int > 1 for exact count.",
    )
    include_models: Optional[str] = Field(
        default=None,
        description=(
            "Comma-separated model ids to compare: lr, lasso, ridge, en, br, huber, knn, dt, rf, "
            "et, ada, gbr, svm, mlp, lightgbm, xgboost, dummy."
        ),
    )
    exclude_models: Optional[str] = Field(
        default=None, description="Comma-separated model ids to leave out of the comparison."
    )
    ignore_gpu_errors: bool = Field(
        default=True, description="Accepted for compatibility; has no effect."
    )

    @model_validator(mode="after")
    def _one_source(self) -> "TrainRegressorInput":
        results.check_one_source(self.feature_set_id, self.input_path, self.holdout_path)
        return self


class RunRegressionInferenceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    input_path: WorkspacePath = Field(
        ..., description="Path to CSV file containing rows to predict."
    )
    model_path: WorkspacePath = Field(
        ..., description="Path to a recorded model file (with or without .pkl), e.g. final_model_path."
    )
    ground_truth_column: Optional[str] = Field(
        default=None,
        description=(
            "Column name holding ground-truth values. "
            "When supplied, MSE, RMSE, MAE, R², MAPE, explained variance, "
            "residual statistics, and a residual analysis table are calculated."
        ),
    )
    prediction_filename: str = Field(
        default="predictions.csv",
        description="Display name of the predictions export.",
    )
    verbose: bool = Field(
        default=True, description="Emit detailed log messages."
    )


# ── Tools ──────────────────────────────────────────────────────────────────────

@tool(
    name="train_regressor",
    visibility={"agent"},
    idempotent=True,
    **manifest.TRAINING,
)
async def train_regressor(params: TrainRegressorInput) -> str | dict[str, Any]:
    """Compare, tune, and blend regression models (scikit-learn, LightGBM, XGBoost)."""
    return await training.train(REGRESSION, params)


@tool(
    name="run_regression_inference",
    visibility={"agent"},
    idempotent=True,
    **manifest.INFERENCE,
)
async def run_regression_inference(params: RunRegressionInferenceInput) -> str | dict[str, Any]:
    """Load a recorded regression model and predict on new data."""
    return await inference.run_inference(REGRESSION, params)
