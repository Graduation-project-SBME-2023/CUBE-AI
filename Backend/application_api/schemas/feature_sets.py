"""Feature-set schemas"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from application_api.core.json_bounds import (
    DOMAIN_JSON_MAX_BYTES,
    FEATURE_MATRIX_MAX_BYTES,
    JsonBoundsError,
    check_json,
    check_versioned_document,
)

FEATURE_SET_SCHEMA_VERSION = 1
# Identity and label columns can never be features.
FORBIDDEN_FEATURE_NAMES = frozenset(
    {"target", "label", "subject_key", "volume_series_instance_uid", "ordinal"}
)
MAX_FEATURES = 20000
MAX_SOURCE_FEATURE_IDS = 50000

Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
ExportPart = Literal["all", "train", "holdout"]


def _versioned(value: Any, what: str) -> Any:
    if value is None:
        return value
    try:
        return check_versioned_document(value, max_bytes=DOMAIN_JSON_MAX_BYTES, what=what)
    except JsonBoundsError as exc:
        raise ValueError(str(exc)) from exc


class FeatureSetWrite(BaseModel):
    cohort_id: UUID
    feature_names: list[str] = Field(min_length=1, max_length=MAX_FEATURES)
    feature_matrix: dict[str, list[Optional[FiniteFloat]]] = Field(min_length=1)
    source_feature_ids: Optional[list[UUID]] = Field(default=None, max_length=MAX_SOURCE_FEATURE_IDS)
    selection: dict[str, Any]
    extraction_config: Optional[dict[str, Any]] = None
    analysis: Optional[dict[str, Any]] = None
    quality_summary: Optional[dict[str, Any]] = None
    config_digest: Digest
    # Required when the set did not come from radiomics rows (the digest of
    # the exact input bytes); recomputed — and compared — when it did.
    source_digest: Optional[Digest] = None
    schema_version: int = Field(default=FEATURE_SET_SCHEMA_VERSION, ge=1)

    @field_validator("feature_names")
    @classmethod
    def _names(cls, value: list[str]) -> list[str]:
        cleaned = [str(name) for name in value]
        if any(not name.strip() for name in cleaned):
            raise ValueError("feature names must not be blank")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("feature names must be unique")
        forbidden = [name for name in cleaned if name.strip().lower() in FORBIDDEN_FEATURE_NAMES]
        if forbidden:
            raise ValueError(
                f"{forbidden} cannot be a feature: the label is joined from the cohort at "
                "read time and never stored in feature_matrix"
            )
        return cleaned

    @field_validator("selection")
    @classmethod
    def _selection(cls, value: dict[str, Any]) -> dict[str, Any]:
        _versioned(value, "selection")
        if not isinstance(value.get("label_supervised_on_full_data"), bool):
            raise ValueError("selection.label_supervised_on_full_data must be a boolean")
        groups = value.get("holdout_groups")
        if groups is not None and not isinstance(groups, list):
            raise ValueError("selection.holdout_groups must be a list of subject keys")
        return value

    @field_validator("extraction_config", "analysis", "quality_summary")
    @classmethod
    def _documents(cls, value):
        return _versioned(value, "feature set document")

    @model_validator(mode="after")
    def _shape(self) -> "FeatureSetWrite":
        width = len(self.feature_names)
        ragged = [key for key, row in self.feature_matrix.items() if len(row) != width]
        if ragged:
            raise ValueError(
                f"feature_matrix is not rectangular: {len(ragged)} row(s) do not have "
                f"{width} value(s), e.g. {ragged[:5]}"
            )
        if any(not str(key).strip() for key in self.feature_matrix):
            raise ValueError("feature_matrix keys are volume UIDs and must not be blank")
        try:
            check_json(self.feature_matrix, max_bytes=FEATURE_MATRIX_MAX_BYTES, what="feature_matrix")
        except JsonBoundsError as exc:
            raise ValueError(str(exc)) from exc
        if self.source_feature_ids is not None:
            if len(set(self.source_feature_ids)) != len(self.source_feature_ids):
                raise ValueError("source_feature_ids must not repeat a radiomics row")
        elif self.source_digest is None:
            raise ValueError(
                "source_digest is required when the set was not selected from "
                "radiomics_features rows (the digest of the exact input bytes)"
            )
        return self


class FeatureSetOut(BaseModel):
    """Catalogue view — never the matrix (a listing must not drag it out of
    TOAST)."""

    feature_set_id: UUID
    created_by_agent_run_id: UUID
    session_id: Optional[UUID] = None
    cohort_id: UUID
    set_key: str
    schema_version: int
    feature_names: list[str]
    feature_count: int
    subject_count: int
    source_feature_count: Optional[int] = None
    selection: dict[str, Any]
    extraction_config: Optional[dict[str, Any]] = None
    analysis: Optional[dict[str, Any]] = None
    quality_summary: Optional[dict[str, Any]] = None
    config_digest: str
    source_digest: str
    manifest_digest: str
    status: str
    frozen_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FeatureSetDetail(FeatureSetOut):
    feature_matrix: dict[str, list[Optional[float]]]
    source_feature_ids: Optional[list[UUID]] = None


class FeatureSetExportRow(BaseModel):
    volume_series_instance_uid: str
    subject_key: str
    ordinal: int
    split: Literal["train", "holdout"]
    target: Any
    values: list[Optional[float]]


class FeatureSetExport(BaseModel):
    """The modeling handoff: features by volume in cohort order, with the label
    joined from the cohort at read time — never stored with them. A row is
    one labelled volume, not one patient: `subject_key` groups a patient's
    rows for the train/holdout split, but does not identify the row."""

    feature_set_id: UUID
    cohort_id: UUID
    set_key: str
    part: ExportPart
    feature_names: list[str]
    label_supervised_on_full_data: bool
    rows: list[FeatureSetExportRow]
