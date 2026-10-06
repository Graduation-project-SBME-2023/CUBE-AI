from sqlalchemy import CheckConstraint, Column, DateTime, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from application_api.db.base import Base

# The owner of every session created before the session-graph migration.
LEGACY_USERNAME = "legacy"


class User(Base):
    """The owner of private sessions. No roles, teams or credentials:
    authentication, when it exists, maps a verified login to `username`."""

    __tablename__ = "users"

    user_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    username = Column(Text, nullable=False, unique=True)
    display_name = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "length(username) > 0 AND username = lower(btrim(username))", name="username_normalised"
        ),
    )
