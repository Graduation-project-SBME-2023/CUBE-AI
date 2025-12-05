from sqlalchemy import Table, Column, Integer, ForeignKey
from core.database import Base

chat_study_association = Table(
    "chat_study_association",
    Base.metadata,
    Column("chat_id", Integer, ForeignKey("chat_sessions.chat_id", ondelete="CASCADE")),
    Column("study_uid", ForeignKey("studies.study_uid", ondelete="CASCADE")),
)
