"""Classification tools on the scikit-learn engine: ``train_classifier`` and
``run_classifier_inference`` (merged plan, Phase 7.1; formerly the PyCaret
tools)."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .._toolkit import tool
from ..common.workspace import WorkspacePath
from . import inference, manifest, results, training
from .engine.estimators import CLASSIFICATION


# ── Input Models ───────────────────────────────────────────────────────────────

class TrainClassifierInput(BaseModel):
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
        default=5, description="Number of cross-validation folds."
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
    fix_imbalance: bool = Field(
        default=True, description="Weight the classes inversely to their frequency (balanced sample weights)."
    )
    data_split_stratify: bool = Field(default=True, description="Stratify the folds and the random split by class.")
    data_split_shuffle: bool = Field(default=True, description="Shuffle before splitting (seeded).")
    preprocess: bool = Field(
        default=True, description="Impute missing values (and scale / transform / PCA when asked)."
    )
    ignore_features: Optional[str] = Field(default=None, description="Comma-separated columns to leave out.")
    numeric_features: Optional[str] = Field(default=None, description="Comma-separated columns to treat as numeric.")
    categorical_features: Optional[str] = Field(
        default=None, description="Comma-separated columns to one-hot encode as categories."
    )
    date_features: Optional[str] = Field(default=None, description="Comma-separated date columns (left out).")
    n_select: int = Field(default=3, description="Number of top compared models to tune and blend.")
    normalize: bool = Field(default=False, description="Standard-scale the numeric features.")
    transformation: bool = Field(default=False, description="Yeo-Johnson transform the numeric features.")
    pca: bool = Field(default=False, description="Reduce dimensionality with PCA.")
    pca_components: Optional[float] = Field(
        default=None, description="PCA: a ratio in (0, 1) of variance to keep, or a component count above 1."
    )
    include_models: Optional[str] = Field(
        default=None,
        description=(
            "Comma-separated model ids to compare: lr, knn, nb, dt, svm, rbfsvm, ridge, rf, et, "
            "ada, gbc, lda, mlp, lightgbm, xgboost, dummy."
        ),
    )
    exclude_models: Optional[str] = Field(default=None, description="Comma-separated model ids to leave out.")
    ignore_gpu_errors: bool = Field(default=True, description="Accepted for compatibility; has no effect.")

    @model_validator(mode="after")
    def _one_source(self) -> "TrainClassifierInput":
        results.check_one_source(self.feature_set_id, self.input_path, self.holdout_path)
        return self


class RunInferenceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    input_path: WorkspacePath = Field(..., description="Path to CSV file containing rows to predict.")
    model_path: WorkspacePath = Field(
        ..., description="Path to a recorded model file (with or without .pkl), e.g. final_model_path."
    )
    ground_truth_column: Optional[str] = Field(default=None)
    prediction_filename: str = Field(default="predictions.csv", description="Display name of the predictions export.")
    verbose: bool = Field(default=True)


# ── Tools ──────────────────────────────────────────────────────────────────────

@tool(
    name="train_classifier",
    visibility={"agent"},
    idempotent=True,
    **manifest.TRAINING,
)
async def train_classifier(params: TrainClassifierInput) -> str | dict[str, Any]:
    """Compare, tune, and blend classification models (scikit-learn, LightGBM, XGBoost)."""
    return await training.train(CLASSIFICATION, params)


@tool(
    name="run_classifier_inference",
    visibility={"agent"},
    idempotent=True,
    **manifest.INFERENCE,
)
async def run_classifier_inference(params: RunInferenceInput) -> str | dict[str, Any]:
    """Load a recorded classification model and generate predictions on new data."""
    return await inference.run_inference(CLASSIFICATION, params)
