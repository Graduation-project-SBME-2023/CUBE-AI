"""Cohort schemas"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from application_api.core.json_bounds import (
    CLINICAL_DATA_MAX_BYTES,
    DOMAIN_JSON_MAX_BYTES,
    JsonBoundsError,
    check_json,
)

LabelSource = Literal["pending", "llm_extraction", "manual_override", "uploaded_targets"]
LabelConfidence = Literal["high", "medium", "low", "unlabeled"]
LabellingMode = Literal["automatic", "manual"]

COHORT_SCHEMA_VERSION = 1
POPULATION_COHORT_KEY = "population"

# The one label definition current produces: a binary disease-presence label. It is
# frozen with the cohort, and a member's label is validated against it.
DEFAULT_LABEL_DEFINITION: dict[str, Any] = {
    "schema_version": COHORT_SCHEMA_VERSION,
    "kind": "binary",
    "field": "has_target_disease",
    "positive_target": 1,
    "negative_target": 0,
}


def _bounded(value: Any, what: str, max_bytes: int = DOMAIN_JSON_MAX_BYTES) -> Any:
    if value is None:
        return value
    try:
        return check_json(value, max_bytes=max_bytes, what=what)
    except JsonBoundsError as exc:
        raise ValueError(str(exc)) from exc


class CohortUpsert(BaseModel):
    """Create a draft cohort, or merge into one that is not yet frozen."""

    criteria: Optional[dict[str, Any]] = None
    label_definition: Optional[dict[str, Any]] = None
    selection_summary: Optional[dict[str, Any]] = None

    @field_validator("criteria", "label_definition", "selection_summary")
    @classmethod
    def _documents_bounded(cls, value):
        return _bounded(value, "cohort document")


class CohortMembersSet(BaseModel):
    """The cohort's volumes, in order. Ordinals follow this list."""

    volume_series_instance_uids: list[str] = Field(default_factory=list, max_length=5000)

    @field_validator("volume_series_instance_uids")
    @classmethod
    def _unique_non_blank(cls, value: list[str]) -> list[str]:
        cleaned = [str(uid).strip() for uid in value]
        if any(not uid for uid in cleaned):
            raise ValueError("volume_series_instance_uids must not contain blank values")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("volume_series_instance_uids must not repeat a volume")
        return cleaned


class CohortLabelWrite(BaseModel):
    volume_series_instance_uid: str = Field(min_length=1)
    has_target_disease: Optional[bool] = None
    label_source: LabelSource
    label_confidence: Optional[LabelConfidence] = None
    label_provenance: Optional[dict[str, Any]] = None
    clinical_data: Optional[dict[str, Any]] = None

    @field_validator("label_provenance")
    @classmethod
    def _provenance_bounded(cls, value):
        return _bounded(value, "label_provenance")

    @field_validator("clinical_data")
    @classmethod
    def _clinical_bounded(cls, value):
        return _bounded(value, "clinical_data", CLINICAL_DATA_MAX_BYTES)


class CohortLabelsWrite(BaseModel):
    labels: list[CohortLabelWrite] = Field(default_factory=list, max_length=5000)


class CohortMemberOut(BaseModel):
    member_id: UUID
    cohort_id: UUID
    study_uid: str
    volume_series_instance_uid: str
    subject_key: str
    ordinal: int
    label: Optional[dict[str, Any]] = None
    label_source: str
    label_confidence: Optional[str] = None
    label_provenance: Optional[dict[str, Any]] = None
    clinical_data: Optional[dict[str, Any]] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CohortOut(BaseModel):
    cohort_id: UUID
    created_by_agent_run_id: UUID
    cohort_key: str
    criteria: dict[str, Any]
    label_definition: dict[str, Any]
    selection_summary: Optional[dict[str, Any]] = None
    frozen_at: Optional[datetime] = None
    created_at: datetime
    members: list[CohortMemberOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class CohortAnalysisRow(BaseModel):
    """One labelled member as a downstream consumer reads it."""

    member_id: UUID
    ordinal: int
    subject_key: str
    study_uid: str
    volume_series_instance_uid: str
    target: int
    label_source: str
    label_confidence: Optional[str] = None
