from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

SegmentationOrigin = Literal["assisted", "pipeline", "imported"]
ArchiveStatus = Literal["available", "unavailable", "quarantined"]


class SegmentationBase(BaseModel):
    label_series_instance_uid: str
    volume_series_instance_uid: str
    # Renamed from segmentation_type / method: the old names
    # described neither what the column held nor how the mask was produced.
    series_description: Optional[str] = None
    target_label: Optional[str] = None
    generation_method: Optional[str] = None
    origin: SegmentationOrigin = "imported"
    is_empty_mask: Optional[bool] = None
    model_id: Optional[UUID] = None
    provenance: Optional[dict[str, Any]] = None
    content_digest: Optional[str] = None
    archive_status: ArchiveStatus = "available"
    created_at: Optional[datetime] = None  # ISO format date-time


class SegmentationCreate(SegmentationBase):
    pass


class SegmentationUpdate(BaseModel):
    series_description: Optional[str] = None
    target_label: Optional[str] = None
    generation_method: Optional[str] = None
    is_empty_mask: Optional[bool] = None
    model_id: Optional[UUID] = None
    provenance: Optional[dict[str, Any]] = None
    content_digest: Optional[str] = None
    archive_status: Optional[ArchiveStatus] = None
    created_at: Optional[datetime] = None  # ISO format date-time


class SegmentationOriginDeclaration(BaseModel):
    """A producer declaring how a mask it just STOW'd was made."""

    label_series_instance_uid: str = Field(min_length=1)
    volume_series_instance_uid: str = Field(min_length=1)
    origin: Literal["assisted", "pipeline"]
    is_empty_mask: Optional[bool] = None
    generation_method: Optional[str] = None
    series_description: Optional[str] = None
    model_name: Optional[str] = None
    provenance: Optional[dict[str, Any]] = None


class Segmentation(SegmentationBase):
    orthanc_series_id: Optional[str] = None
    # The final review decision of a submitted segmentation review that
    # selected (or rejected) this mask. Read-only; lives on segmentation_pairs.
    review_decision: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
