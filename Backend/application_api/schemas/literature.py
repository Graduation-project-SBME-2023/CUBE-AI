"""Literature schemas"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LiteratureReviewCreate(BaseModel):
    """Create a new literature review for a planning-phase search."""

    session_id: UUID
    review_key: str = Field(min_length=1, max_length=500)
    # Versioned JSONB: the PICO / search parameters that drove this review.
    query: dict[str, Any]
    result_summary: Optional[dict[str, Any]] = None


class LiteraturePaperIn(BaseModel):
    """One paper to add or deduplicate in a literature review."""

    pmid: Optional[str] = Field(default=None, min_length=1, max_length=50)
    doi: Optional[str] = Field(default=None, min_length=1, max_length=500)
    title: str = Field(min_length=1)
    authors: Optional[list[str]] = None
    year: Optional[int] = Field(default=None, gt=0, lt=10000)
    abstract: Optional[str] = None
    journal: Optional[str] = None
    relevance_score: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    tier: Optional[str] = None
    category: Optional[str] = None
    topic: Optional[str] = None
    similar_study: Optional[bool] = None
    citation_metadata: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def _has_identifier(self) -> "LiteraturePaperIn":
        if self.pmid is None and self.doi is None:
            raise ValueError("At least one of pmid and doi is required")
        return self


class LiteraturePaperOut(BaseModel):
    paper_id: UUID
    literature_review_id: UUID
    pmid: Optional[str] = None
    doi: Optional[str] = None
    title: str
    authors: Optional[list[str]] = None
    year: Optional[int] = None
    abstract: Optional[str] = None
    journal: Optional[str] = None
    relevance_score: Optional[float] = None
    tier: Optional[str] = None
    category: Optional[str] = None
    topic: Optional[str] = None
    similar_study: Optional[bool] = None
    citation_metadata: Optional[dict[str, Any]] = None

    model_config = ConfigDict(from_attributes=True)


class LiteratureReviewOut(BaseModel):
    literature_review_id: UUID
    session_id: UUID
    review_key: str
    query: dict[str, Any]
    result_summary: Optional[dict[str, Any]] = None
    schema_version: int
    created_at: datetime
    papers: list[LiteraturePaperOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class LiteratureReviewSummaryOut(BaseModel):
    """Lightweight review entry for list responses (no papers)."""

    literature_review_id: UUID
    session_id: UUID
    review_key: str
    schema_version: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
