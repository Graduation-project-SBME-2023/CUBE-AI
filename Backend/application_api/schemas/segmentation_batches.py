"""Segmentation batch schemas"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from application_api.core.json_bounds import DOMAIN_JSON_MAX_BYTES, JsonBoundsError, check_json

PairStatus = Literal["pending", "running", "ready", "failed"]
ReviewDecision = Literal["accepted", "modified", "rejected"]
StageReviewStatus = Literal["not_ready", "pending"]

SEGMENTATION_SCHEMA_VERSION = 1


def pair_key(volume_series_instance_uid: str, target_key: str) -> str:
    return f"{volume_series_instance_uid}__{target_key}"


def _versioned(value: Any, what: str) -> Any:
    if value is None:
        return value
    if not isinstance(value, dict):
        raise ValueError(f"{what} must be a JSON object")
    value = dict(value)
    value.setdefault("schema_version", SEGMENTATION_SCHEMA_VERSION)
    try:
        return check_json(value, max_bytes=DOMAIN_JSON_MAX_BYTES, what=what)
    except JsonBoundsError as exc:
        raise ValueError(str(exc)) from exc


class TargetIn(BaseModel):
    target_key: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=512)
    specification: dict[str, Any]
    model_name: Optional[str] = None

    @field_validator("specification")
    @classmethod
    def _spec(cls, value):
        return _versioned(value, "target specification")


class PairIn(BaseModel):
    target_key: str = Field(min_length=1)
    volume_series_instance_uid: str = Field(min_length=1)
    status: PairStatus = "pending"
    inference_job_id: Optional[str] = None
    candidate_segmentation_uid: Optional[str] = None
    execution_checkpoint: Optional[dict[str, Any]] = None

    @field_validator("execution_checkpoint")
    @classmethod
    def _execution_checkpoint(cls, value):
        return _versioned(value, "pair execution checkpoint")


class SegmentationBatchCreate(BaseModel):
    """Create a batch with its targets and pairs in one transaction."""

    cohort_id: UUID
    config: dict[str, Any]
    targets: list[TargetIn] = Field(min_length=1, max_length=64)
    pairs: list[PairIn] = Field(min_length=1, max_length=20000)

    @field_validator("config")
    @classmethod
    def _config(cls, value):
        return _versioned(value, "segmentation config")


class PairStateUpdate(BaseModel):
    """A pair's progress. Identity is the pair key; the row is never replaced."""

    pair_key: str = Field(min_length=1)
    status: PairStatus
    inference_job_id: Optional[str] = None
    candidate_segmentation_uid: Optional[str] = None
    execution_checkpoint: Optional[dict[str, Any]] = None

    @field_validator("execution_checkpoint")
    @classmethod
    def _execution_checkpoint(cls, value):
        return _versioned(value, "pair execution checkpoint")


class PairStatesUpdate(BaseModel):
    pairs: list[PairStateUpdate] = Field(min_length=1, max_length=20000)
    review_status: Optional[StageReviewStatus] = None


class ReviewDraft(BaseModel):
    decisions: dict[str, ReviewDecision]


class ReviewSubmission(BaseModel):
    """The one final review. `request_hash` is derived from the decisions."""

    accepted: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    modified: list[str] = Field(default_factory=list)
    selected: dict[str, str] = Field(default_factory=dict)

    def request_hash(self) -> str:
        return review_request_hash(self.accepted, self.rejected, self.modified)


def review_request_hash(accepted, rejected, modified) -> str:
    canonical = json.dumps(
        {
            "accepted": sorted({str(item) for item in accepted or []}),
            "rejected": sorted({str(item) for item in rejected or []}),
            "modified": sorted({str(item) for item in modified or []}),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ReviewBegin(BaseModel):
    request_hash: str = Field(pattern="^[0-9a-f]{64}$")


class TargetOut(BaseModel):
    target_id: UUID
    segmentation_batch_id: UUID
    target_key: str
    label: str
    specification: dict[str, Any]
    inference_model_id: Optional[UUID] = None

    model_config = ConfigDict(from_attributes=True)


class PairOut(BaseModel):
    pair_id: UUID
    pair_key: str
    segmentation_batch_id: UUID
    target_id: UUID
    target_key: str
    volume_series_instance_uid: str
    cohort_id: UUID
    cohort_member_id: UUID
    subject_key: Optional[str] = None
    ordinal: Optional[int] = None
    inference_job_id: Optional[str] = None
    status: str
    candidate_segmentation_uid: Optional[str] = None
    selected_segmentation_uid: Optional[str] = None
    segment_number: int
    review_decision: Optional[str] = None
    execution_checkpoint: Optional[dict[str, Any]] = None
    created_at: datetime


class SegmentationBatchOut(BaseModel):
    segmentation_batch_id: UUID
    created_by_run_id: UUID
    session_id: UUID
    cohort_id: UUID
    batch_key: str
    config: dict[str, Any]
    review_status: str
    review_request_hash: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime
    targets: list[TargetOut] = Field(default_factory=list)
    pairs: list[PairOut] = Field(default_factory=list)
