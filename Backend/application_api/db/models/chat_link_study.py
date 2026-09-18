from sqlalchemy import Table, Column, ForeignKey, PrimaryKeyConstraint
from sqlalchemy.dialects.postgresql import UUID
from application_api.db.base import Base

# The composite primary key was dropped by migration 6664d514406c during the
# UUID conversion (dropping chat_id dropped the PK it participated in) and
# was never restored. Without it, duplicate (chat_id, study_uid) rows are
# possible at the database level.
chat_study_association = Table(
    "chat_study_association",
    Base.metadata,
    Column("chat_id", UUID(as_uuid=True), ForeignKey("chat_sessions.chat_id", ondelete="CASCADE")),
    Column("study_uid", ForeignKey("studies.study_uid", ondelete="CASCADE")),
    PrimaryKeyConstraint("chat_id", "study_uid", name="pk_chat_study_association"),
)
