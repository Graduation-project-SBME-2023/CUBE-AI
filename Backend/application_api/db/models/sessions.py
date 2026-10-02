from sqlalchemy import CheckConstraint, Column, DateTime, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from application_api.db.base import Base
from .chats import Chat
from .session_events import SessionEvent

# current. Renamed from `chat_sessions`: the session is the top level
#  a chat is one conversation inside it.
SESSION_CONTROL_STATES = (
    "planning",
    "ready",
    "running",
    "pausing",
    "paused",
    "completed",
    "failed",
    "archived",
)
PROMPT_KINDS = ("hypothesis", "general")


class Session(Base):
    __tablename__ = "sessions"

    session_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    title = Column(String(255))

    # The user's own words. Required for every new session and immutable once
    # written — a database trigger enforces that, because the convention is
    # what failed: the planner used to overwrite the only copy of this text.
    # NULL is reachable only for legacy rows that carry
    # error.code='legacy_prompt_unavailable' and control_state='archived'.
    prompt = Column(Text, nullable=True)
    prompt_kind = Column(String, nullable=False, server_default="hypothesis")
    # The planner's structured reading of the prompt — versioned, and never a
    # copy of the prompt text.
    intent = Column(JSONB, nullable=True)

    # Pause is durable through guarded updates: running -> pausing -> paused.
    # no is_active, no pause timestamp, no recovery cursor, no runtime tag,
    # no session lease, no event counter, no generic version column.
    control_state = Column(String, nullable=False, server_default="planning", index=True)
    error = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # RESTRICT, not delete-orphan: provenance blocks a hard delete, and
    # `archived` is normal removal.
    chats = relationship("Chat", back_populates="session", lazy="selectin")
    # Exactly one session graph, enforced by the shared PK plus the deferred
    # existence trigger.
    session_graph = relationship("SessionGraph", back_populates="session", uselist=False)
    events = relationship("SessionEvent", back_populates="session", lazy="selectin")

    __table_args__ = (
        CheckConstraint(
            "control_state IN ('planning', 'ready', 'running', 'pausing', 'paused', "
            "'completed', 'failed', 'archived')",
            name="control_state",
        ),
        CheckConstraint(
            "prompt_kind IN ('hypothesis', 'general')", name="prompt_kind"
        ),
        # A NULL prompt is only reachable for an archived legacy row.
        CheckConstraint(
            "prompt IS NOT NULL OR control_state = 'archived'", name="prompt_present"
        ),
        CheckConstraint(
            "prompt IS NULL OR btrim(prompt) <> ''", name="prompt_not_blank"
        ),
    )
