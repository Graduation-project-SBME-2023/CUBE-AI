"""Literature-review ORM models"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from application_api.db.base import Base

# Mirrors application_api/core/json_bounds.py.
_DOMAIN_JSON_MAX_BYTES = 1024 * 1024


def _versioned(column: str, *, nullable: bool) -> str:
    rule = (
        f"(jsonb_typeof({column}) = 'object' "
        f"AND jsonb_typeof({column}->'schema_version') = 'number' "
        f"AND pg_column_size({column}) <= {_DOMAIN_JSON_MAX_BYTES})"
    )
    return f"({column} IS NULL OR {rule})" if nullable else rule


class LiteratureReview(Base):
    """One planning-phase literature search."""

    __tablename__ = "literature_reviews"

    literature_review_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    review_key = Column(Text, nullable=False)
    # Versioned JSONB: the PICO / search parameters that drove this review.
    query = Column(JSONB, nullable=False)
    # Versioned JSONB: the compiled context (no paper text, no label data).
    result_summary = Column(JSONB(none_as_null=True), nullable=True)
    schema_version = Column(Integer, nullable=False, server_default="1")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("session_id", "review_key", name="uq_literature_reviews_session_key"),
        CheckConstraint("length(btrim(review_key)) > 0", name="review_key_not_blank"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint(_versioned("query", nullable=False), name="query_versioned"),
        CheckConstraint(_versioned("result_summary", nullable=True), name="result_summary_versioned"),
    )


class LiteraturePaper(Base):
    """One paper in a planning-phase literature review."""

    __tablename__ = "literature_papers"

    paper_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    literature_review_id = Column(
        UUID(as_uuid=True),
        ForeignKey("literature_reviews.literature_review_id", ondelete="RESTRICT"),
        nullable=False,
    )
    pmid = Column(Text, nullable=True)
    doi = Column(Text, nullable=True)
    title = Column(Text, nullable=False)
    authors = Column(JSONB(none_as_null=True), nullable=True)
    year = Column(Integer, nullable=True)
    abstract = Column(Text, nullable=True)
    journal = Column(Text, nullable=True)
    relevance_score = Column(Numeric(5, 2), nullable=True)
    tier = Column(Text, nullable=True)
    category = Column(Text, nullable=True)
    topic = Column(Text, nullable=True)
    similar_study = Column(Boolean, nullable=True)
    citation_metadata = Column(JSONB(none_as_null=True), nullable=True)
    schema_version = Column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        Index("ix_literature_papers_review_id", "literature_review_id"),
        # Deduplication keys. NULLs stay distinct, so a review can hold many
        # papers that have only a DOI, or only a PMID.
        UniqueConstraint(
            "literature_review_id",
            "pmid",
            name="uq_literature_papers_review_pmid",
            postgresql_nulls_not_distinct=False,
        ),
        UniqueConstraint(
            "literature_review_id",
            "doi",
            name="uq_literature_papers_review_doi",
            postgresql_nulls_not_distinct=False,
        ),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint(
            "pmid IS NOT NULL OR doi IS NOT NULL",
            name="has_identifier",
        ),
        CheckConstraint(
            "pmid IS NULL OR length(btrim(pmid)) > 0",
            name="pmid_not_blank",
        ),
        CheckConstraint(
            "doi IS NULL OR length(btrim(doi)) > 0",
            name="doi_not_blank",
        ),
        CheckConstraint("year IS NULL OR year > 0", name="year_positive"),
        CheckConstraint(
            "relevance_score IS NULL OR (relevance_score >= 0 AND relevance_score <= 100)",
            name="relevance_score_bounded",
        ),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
    )
