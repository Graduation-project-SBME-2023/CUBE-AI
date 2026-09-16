"""Radiomics schemas"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from application_api.core.json_bounds import DOMAIN_JSON_MAX_BYTES, JsonBoundsError, check_json

RADIOMICS_SCHEMA_VERSION = 1
# One batch is one request: bounded so a request stays well under the body
# limits and one bad row fails a small, retryable unit.
RADIOMICS_BATCH_MAX_ROWS = 200

Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]


def _bounded(value: Any, what: str) -> Any:
    try:
        return check_json(value, max_bytes=DOMAIN_JSON_MAX_BYTES, what=what)
    except JsonBoundsError as exc:
        raise ValueError(str(exc)) from exc


class RadiomicsFeatureWrite(BaseModel):
    label_series_instance_uid: str = Field(min_length=1)
    source_volume_uid: str = Field(min_length=1)
    segment_number: int = Field(gt=0)
    segmentation_pair_id: Optional[UUID] = None
    # Finite numbers, or null where the extractor produced no finite value;
    # `configuration.missing_value_policy` records what a null means.
    features: dict[str, Optional[FiniteFloat]] = Field(min_length=1)
    extractor_name: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    configuration: dict[str, Any]
    config_digest: Digest
    image_digest: Digest
    segmentation_digest: Digest
    source_digest: Digest
    schema_version: int = Field(default=RADIOMICS_SCHEMA_VERSION, ge=1)

    @field_validator("features")
    @classmethod
    def _features_bounded(cls, value):
        return _bounded(value, "radiomics features")

    @field_validator("configuration")
    @classmethod
    def _configuration_bounded(cls, value):
        return _bounded(value, "extraction configuration")


class RadiomicsBatchWrite(BaseModel):
    rows: list[RadiomicsFeatureWrite] = Field(min_length=1, max_length=RADIOMICS_BATCH_MAX_ROWS)


class RadiomicsFeatureRef(BaseModel):
    id: UUID
    label_series_instance_uid: str
    segment_number: int
    source_digest: str
    created: bool


class RadiomicsBatchResult(BaseModel):
    created: int
    reused: int
    rows: list[RadiomicsFeatureRef]


class RadiomicsFeatureOut(BaseModel):
    """A catalogue row. Legacy rows carry NULL provenance and say so."""

    id: UUID
    session_id: Optional[UUID] = None
    label_series_instance_uid: Optional[str] = None
    source_volume_uid: Optional[str] = None
    segment_number: Optional[int] = None
    segmentation_pair_id: Optional[UUID] = None
    created_by_agent_run_id: Optional[UUID] = None
    extractor_name: Optional[str] = None
    extractor_version: Optional[str] = None
    config_digest: Optional[str] = None
    image_digest: Optional[str] = None
    segmentation_digest: Optional[str] = None
    source_digest: Optional[str] = None
    schema_version: Optional[int] = None
    created_at: datetime
    # Joined through the segmentation pair when there is one.
    target_key: Optional[str] = None
    subject_key: Optional[str] = None
    provenance_complete: bool
    features: Optional[dict[str, Any]] = None
    configuration: Optional[dict[str, Any]] = None

    model_config = ConfigDict(from_attributes=True)
