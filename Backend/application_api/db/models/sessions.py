from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from application_api.db.base import Base
from .chats import Chat
from .session_events import SessionEvent

# current. Renamed from `chat_sessions`: the session is the top level
#  a chat is one conversation inside it.
# Control intent only. Progress -- idle, running, pausing,
# awaiting approval, blocked -- is derived from runs and attempts, never stored.
SESSION_CONTROL_STATES = ("active", "paused", "archived")
PROMPT_KINDS = ("hypothesis", "general")
EXECUTION_MODES = ("auto", "plan")  # Auto is the default, Plan is opt-in
PRIVACY_MODES = ("standard", "phi_local")


class Session(Base):
    __tablename__ = "sessions"

    session_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # The single owner. Immutable; every row a session owns inherits it.
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    title = Column(String(255))

    # The user's own words. Required for every new session and immutable once
    # written — a database trigger enforces that, because the convention is
    # what failed: the planner used to overwrite the only copy of this text.
    # NULL is reachable only for legacy rows that carry
    # error.code='legacy_prompt_unavailable' and control_state='archived'.
    prompt = Column(Text, nullable=True)
    prompt_kind = Column(Text, nullable=False, server_default="hypothesis")
    # The planner's structured reading of the prompt — versioned, and never a
    # copy of the prompt text.
    intent = Column(JSONB, nullable=True)

    # The user's intent: active, paused or archived. Written only by the
    # control-state route, never by execution progress.
    control_state = Column(Text, nullable=False, server_default="active", index=True)
    error = Column(JSONB, nullable=True)

    # Durable settings: a restart must never switch Auto/Plan, weaken
    # PHI protection or reset the budget.
    execution_mode = Column(Text, nullable=False, server_default="auto")
    privacy_mode = Column(Text, nullable=False, server_default="standard")
    # Advanced once per attempt end and once per Main turn.
    tokens_used = Column(BigInteger, nullable=False, server_default="0")
    # The inbox cursor: every waking event <= this id is handled.
    inbox_handled_through_event_id = Column(BigInteger, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # RESTRICT, not delete-orphan: provenance blocks a hard delete, and
    # `archived` is normal removal.
    chats = relationship("Chat", back_populates="session", lazy="raise")
    # Exactly one session graph, enforced by the shared PK plus the deferred
    # existence trigger.
    session_graph = relationship("SessionGraph", back_populates="session", uselist=False)
    events = relationship("SessionEvent", back_populates="session", lazy="raise")

    __table_args__ = (
        CheckConstraint(
            "control_state IN ('active', 'paused', 'archived')", name="control_state"
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
        CheckConstraint("execution_mode IN ('plan', 'auto')", name="execution_mode"),
        CheckConstraint("privacy_mode IN ('standard', 'phi_local')", name="privacy_mode"),
        CheckConstraint("tokens_used >= 0", name="tokens_used_nonnegative"),
        CheckConstraint(
            "inbox_handled_through_event_id IS NULL OR inbox_handled_through_event_id >= 0",
            name="inbox_cursor_nonnegative",
        ),
        Index("ix_sessions_user_id_updated_at", "user_id", text("updated_at DESC")),
        Index("ix_sessions_updated_at", text("updated_at DESC")),
    )
