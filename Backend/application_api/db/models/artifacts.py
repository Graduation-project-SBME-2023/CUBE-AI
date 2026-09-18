from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from application_api.core.artifact_limits import (
    ARTIFACT_MAX_BYTES,
    ARTIFACT_METADATA_MAX_BYTES,
)
from application_api.db.base import Base


class Artifact(Base):
    """Retained bytes, and the one catalogued exception."""

    __tablename__ = "artifacts"

    artifact_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Nullable for planning-phase literature artifacts, which have no agent run
    # to hang off. No standalone FK here: the composite
    # (created_by_agent_run_id, session_id) FK below already proves existence
    # and same-session ownership together.
    created_by_agent_run_id = Column(
        UUID(as_uuid=True),
        nullable=True,
    )

    kind = Column(Text, nullable=False)
    logical_key = Column(Text, nullable=False)
    display_filename = Column(Text, nullable=False)
    mime_type = Column(Text, nullable=False)
    byte_size = Column(BigInteger, nullable=False)
    sha256 = Column(Text, nullable=False)

    body = Column(LargeBinary, nullable=True)
    render_path = Column(Text, nullable=True)

    metadata_ = Column("metadata", JSONB(none_as_null=True), nullable=True)
    schema_version = Column(Integer, nullable=False, server_default="1")
    retention_class = Column(Text, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("session_id", "logical_key", name="uq_artifacts_session_id"),
        # When the run link is present it must be a run of THIS session.
        ForeignKeyConstraint(
            ["created_by_agent_run_id", "session_id"],
            ["agent_runs.agent_run_id", "agent_runs.session_id"],
            name="fk_artifacts_created_by_agent_run_id_session_id_agent_runs",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "kind IN ('figure_png', 'figure_jpeg', 'figure_gif', 'export_csv', 'input_csv')",
            name="kind",
        ),
        CheckConstraint(
            "retention_class IN ('user_retained', 'system_render')",
            name="retention_class",
        ),
        CheckConstraint("length(btrim(logical_key)) > 0", name="logical_key_not_blank"),
        CheckConstraint(
            "length(btrim(display_filename)) > 0", name="display_filename_not_blank"
        ),
        CheckConstraint("length(btrim(mime_type)) > 0", name="mime_type_not_blank"),
        CheckConstraint(
            f"byte_size > 0 AND byte_size <= {ARTIFACT_MAX_BYTES}",
            name="byte_size_positive",
        ),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_is_a_digest"),
        CheckConstraint(
            f"coalesce(pg_column_size(metadata), 0) <= {ARTIFACT_METADATA_MAX_BYTES}",
            name="metadata_bounded",
        ),
        # , as one constraint rather than as prose.
        CheckConstraint(
            "(retention_class = 'system_render' AND body IS NULL AND render_path IS NOT NULL)"
            " OR "
            "(retention_class = 'user_retained' AND body IS NOT NULL AND render_path IS NULL)",
            name="retention_shape",
        ),
        CheckConstraint(
            "render_path IS NULL OR ("
            r"render_path !~ '^([a-zA-Z]:)?[\\/]'"
            r" AND render_path !~ '(^|[\\/])\.\.([\\/]|$)'"
            r" AND render_path !~ '^[a-zA-Z][a-zA-Z0-9+.-]*://')",
            name="render_path_is_relative",
        ),
        CheckConstraint(
            "body IS NULL OR octet_length(body) = byte_size",
            name="body_matches_byte_size",
        ),
        Index("ix_artifacts_session_id_kind", "session_id", "kind"),
        Index("ix_artifacts_created_by_agent_run_id", "created_by_agent_run_id"),
    )
