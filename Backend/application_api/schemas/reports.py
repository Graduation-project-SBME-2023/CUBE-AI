"""Report schemas"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


_Key = Annotated[str, StringConstraints(min_length=1, max_length=500)]


class ReportCreate(BaseModel):
    """Create a new report row with initial source content."""

    session_id: UUID
    created_by_run_id: Optional[UUID] = None
    literature_review_id: Optional[UUID] = None
    source_text: str = Field(min_length=1)
    source_format: str = Field(default="latex", pattern=r"^(latex|markdown)$")
    title: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _exclusive_source(self) -> "ReportCreate":
        has_run = self.created_by_run_id is not None
        has_lit = self.literature_review_id is not None
        if has_run == has_lit:
            raise ValueError(
                "Exactly one of created_by_run_id and literature_review_id is required (R13)"
            )
        return self


class ReportPublish(BaseModel):
    """Compile-then-publish request."""

    latex_text: str = Field(min_length=1)
    expected_version: int = Field(ge=1)
    image_keys: list[str] = Field(default_factory=list, max_length=200)


class ReportImageIn(BaseModel):
    """Register one stored file as a named source image for a report."""

    file_id: UUID
    image_key: _Key


class ReportImageOut(BaseModel):
    image_id: UUID
    report_id: UUID
    file_id: UUID
    image_key: str

    model_config = ConfigDict(from_attributes=True)


class ReportOut(BaseModel):
    report_id: UUID
    session_id: UUID
    created_by_run_id: Optional[UUID] = None
    literature_review_id: Optional[UUID] = None
    source_text: str
    source_format: str
    source_version: int
    title: Optional[str] = None
    schema_version: int
    created_at: datetime
    updated_at: datetime
    images: list[ReportImageOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ReportSummaryOut(BaseModel):
    """Lightweight report entry for list responses (no source_text)."""

    report_id: UUID
    session_id: UUID
    created_by_run_id: Optional[UUID] = None
    literature_review_id: Optional[UUID] = None
    source_format: str
    source_version: int
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
