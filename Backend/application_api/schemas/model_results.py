"""Model-result schemas"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from application_api.core.json_bounds import (
    DOMAIN_JSON_MAX_BYTES,
    JsonBoundsError,
    check_versioned_document,
)

MODEL_RESULT_SCHEMA_VERSION = 1
MAX_VARIANTS = 100

Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Key = Annotated[str, StringConstraints(min_length=1, max_length=200, pattern=r"^[^/\\]+$")]
VariantStatus = Literal["building", "ready", "failed"]


def _versioned(value: Any, what: str) -> Any:
    if value is None:
        return value
    try:
        return check_versioned_document(value, max_bytes=DOMAIN_JSON_MAX_BYTES, what=what)
    except JsonBoundsError as exc:
        raise ValueError(str(exc)) from exc


class ModelVariantWrite(BaseModel):
    variant_key: Key
    algorithm: str = Field(min_length=1, max_length=500)
    parameters: dict[str, Any]
    metrics: Optional[dict[str, Any]] = None
    predictions_artifact_id: Optional[UUID] = None
    # Relative to the workspace, e.g. `modeling/models/<comparison>/<variant>.pkl`.
    model_file_name: Optional[str] = Field(default=None, max_length=1024)
    model_file_digest: Optional[Digest] = None
    selected: bool = False
    status: VariantStatus = "ready"

    @field_validator("parameters", "metrics")
    @classmethod
    def _documents(cls, value):
        return _versioned(value, "model result document")

    @field_validator("model_file_name")
    @classmethod
    def _relative(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.replace("\\", "/").strip()
        if normalized.startswith("/") or ":" in normalized.split("/")[0]:
            raise ValueError("model_file_name is relative to the workspace, never a host path")
        if any(part == ".." for part in normalized.split("/")):
            raise ValueError("model_file_name must not traverse out of the workspace")
        return normalized

    @model_validator(mode="after")
    def _shape(self) -> "ModelVariantWrite":
        if (self.model_file_name is None) != (self.model_file_digest is None):
            raise ValueError("model_file_name and model_file_digest are both present or both absent")
        if self.status == "ready" and self.metrics is None:
            raise ValueError("a ready variant carries its metrics")
        if self.selected and self.status != "ready":
            raise ValueError("only a ready variant can be the selected one")
        return self


class ModelComparisonWrite(BaseModel):
    cohort_id: UUID
    feature_set_id: Optional[UUID] = None
    input_artifact_id: Optional[UUID] = None
    dataset_digest: Digest
    preprocessing: dict[str, Any]
    feature_order: dict[str, Any]
    split_definition: dict[str, Any]
    seeds: dict[str, Any]
    environment: dict[str, Any]
    variants: list[ModelVariantWrite] = Field(min_length=1, max_length=MAX_VARIANTS)

    @field_validator("preprocessing", "feature_order", "split_definition", "seeds", "environment")
    @classmethod
    def _documents(cls, value):
        return _versioned(value, "model comparison document")

    @model_validator(mode="after")
    def _shape(self) -> "ModelComparisonWrite":
        if self.feature_set_id is None and self.input_artifact_id is None:
            raise ValueError(
                "a model trained without a feature set must capture its exact input bytes "
                "as an input_csv artifact (R6)"
            )
        keys = [variant.variant_key for variant in self.variants]
        if len(set(keys)) != len(keys):
            raise ValueError("variant keys must be unique within a comparison")
        if sum(variant.selected for variant in self.variants) > 1:
            raise ValueError("at most one variant of a comparison is selected")
        features = self.feature_order.get("features")
        if not isinstance(features, list) or not all(isinstance(item, str) for item in features):
            raise ValueError("feature_order.features is the ordered list of feature names")
        return self


class ModelResultOut(BaseModel):
    model_result_id: UUID
    created_by_agent_run_id: UUID
    session_id: Optional[UUID] = None
    cohort_id: UUID
    feature_set_id: Optional[UUID] = None
    comparison_key: str
    variant_key: str
    algorithm: str
    parameters: Optional[dict[str, Any]] = None
    preprocessing: Optional[dict[str, Any]] = None
    feature_order: Optional[dict[str, Any]] = None
    split_definition: Optional[dict[str, Any]] = None
    seeds: Optional[dict[str, Any]] = None
    environment: Optional[dict[str, Any]] = None
    metrics: Optional[dict[str, Any]] = None
    dataset_digest: str
    predictions_artifact_id: Optional[UUID] = None
    input_artifact_id: Optional[UUID] = None
    selected: bool
    model_file_name: Optional[str] = None
    model_file_digest: Optional[str] = None
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ModelComparisonOut(BaseModel):
    comparison_key: str
    created_by_agent_run_id: UUID
    session_id: UUID
    cohort_id: UUID
    feature_set_id: Optional[UUID] = None
    input_artifact_id: Optional[UUID] = None
    dataset_digest: str
    selected_variant_key: Optional[str] = None
    variants: list[ModelResultOut]
