"""Report ORM models """

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from application_api.db.base import Base

REPORT_SOURCE_FORMATS = ("latex", "markdown")


class Report(Base):
    """Durable editable manuscript source """

    __tablename__ = "reports"

    report_id = Column(
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
    # Exactly one of the next two is non-NULL. No separate `index=True` here:
    # the unique constraints below already back every non-null lookup on
    # these columns, so a second plain index would only duplicate them.
    created_by_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("node_runs.run_id", ondelete="RESTRICT"),
        nullable=True,
    )
    literature_review_id = Column(
        UUID(as_uuid=True),
        ForeignKey("literature_reviews.literature_review_id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_text = Column(Text, nullable=False)
    source_format = Column(Text, nullable=False, server_default="latex")
    # Optimistic-concurrency token: incremented by every successful publish.
    source_version = Column(Integer, nullable=False, server_default="1")
    title = Column(Text, nullable=True)
    schema_version = Column(Integer, nullable=False, server_default="1")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        # One report per research run. A plain (non-partial) UNIQUE constraint,
        # to match what the migration actually creates: multiple NULLs are
        # still allowed (Postgres does not consider NULL to equal NULL in a
        # unique constraint), so this behaves like a partial index over the
        # non-null rows without needing postgresql_where.
        UniqueConstraint("created_by_run_id", name="uq_reports_created_by_run_id"),
        # One report per literature review.
        UniqueConstraint("literature_review_id", name="uq_reports_literature_review_id"),
        # exactly one source.
        CheckConstraint(
            "(created_by_run_id IS NOT NULL) != (literature_review_id IS NOT NULL)",
            name="exclusive_source",
        ),
        CheckConstraint(
            "source_format IN ('latex', 'markdown')",
            name="source_format",
        ),
        CheckConstraint("length(btrim(source_text)) > 0", name="source_text_not_blank"),
        CheckConstraint("source_version >= 1", name="source_version_positive"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
    )


class ReportImage(Base):
    """An image registered for use in a report."""

    __tablename__ = "report_images"

    image_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    report_id = Column(
        UUID(as_uuid=True),
        ForeignKey("reports.report_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    file_id = Column(
        UUID(as_uuid=True),
        ForeignKey("stored_files.file_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # The key used in \includegraphics; basename only, no path traversal.
    image_key = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("report_id", "image_key", name="uq_report_images_report_key"),
        CheckConstraint("length(btrim(image_key)) > 0", name="image_key_not_blank"),
        CheckConstraint(
            r"image_key !~ '^([a-zA-Z]:)?[/\\]'"
            r" AND image_key !~ '(^|[/\\])\.\.[/\\]'"
            r" AND image_key !~ '^[a-zA-Z][a-zA-Z0-9+.-]*://'",
            name="image_key_is_safe",
        ),
    )
