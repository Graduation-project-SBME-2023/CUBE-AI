from sqlalchemy import Table, Column, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from core.database import Base

chat_study_association = Table(
    "chat_study_association",
    Base.metadata,
    Column("chat_id", UUID(as_uuid=True), ForeignKey("chat_sessions.chat_id", ondelete="CASCADE")),
    Column("study_uid", ForeignKey("studies.study_uid", ondelete="CASCADE")),
)
