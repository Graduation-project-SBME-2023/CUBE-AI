"""Contract tests for the current session spine."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APPLICATION_ROOT = BACKEND_ROOT / "application_api"
AGENT_ROOT = BACKEND_ROOT / "agent_system"
VERSIONS = APPLICATION_ROOT / "migrations" / "versions"


def _metadata():
    import application_api.db.models  # noqa: F401
    from application_api.db.base import Base

    return Base.metadata


def _migration(revision: str) -> str:
    for path in VERSIONS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if f'revision: str = "{revision}"' in text:
            return text
    raise AssertionError(f"no migration declares revision {revision}")


# ----------------------------------------------------------------------
# Step C — the prompt
# ----------------------------------------------------------------------
def test_sessions_table_replaces_chat_sessions():
    tables = set(_metadata().tables)
    assert "sessions" in tables
    assert "chat_sessions" not in tables


def test_sessions_carries_the_raw_prompt_and_its_kind():
    columns = _metadata().tables["sessions"].columns
    assert "prompt" in columns
    assert "prompt_kind" in columns
    assert columns["prompt_kind"].nullable is False
    # The planner's structured reading sits BESIDE the prompt, never on top.
    assert "intent" in columns


def test_sessions_drops_the_columns_a4_removed():
    columns = set(_metadata().tables["sessions"].columns.keys())
    for dead in (
        "is_active",
        "summary",
        "last_activity",  # moved to chats in step D
        "paused_at",
        "recovery_cursor",
        "runtime_tag",
        "lease",
        "event_counter",
        "version",
    ):
        assert dead not in columns


def test_prompt_is_immutable_at_the_database_level():
    """A convention is what failed here, so current uses a trigger."""
    migration = _migration("c1f0a2c3d4e5")
    assert "sessions_reject_prompt_mutation" in migration
    assert "CREATE TRIGGER sessions_prompt_is_immutable" in migration
    assert "sessions.prompt is immutable once written" in migration


def test_a_null_prompt_is_only_reachable_for_an_archived_legacy_row():
    """Unrecoverable prompts are reported, never guessed."""
    checks = {
        constraint.name
        for constraint in _metadata().tables["sessions"].constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert "ck_sessions_prompt_present" in checks

    migration = _migration("c1f0a2c3d4e5")
    assert "legacy_prompt_unavailable" in migration
    assert "CHECK (prompt IS NOT NULL OR status = 'archived')" in migration
    # The count is reported rather than silently swallowed.
    assert "prompt backfill" in migration


def test_prompt_backfill_prefers_the_original_user_message():
    """`hypotheses.hypothesis` may already BE the planner's rewrite, so the
    earliest user message is tried first."""
    migration = _migration("c1f0a2c3d4e5")
    message_backfill = migration.index("FROM chat_messages")
    hypothesis_backfill = migration.index("FROM hypotheses h\n            WHERE h.chat_id")
    assert message_backfill < hypothesis_backfill


def test_session_create_requires_a_non_empty_prompt():
    from application_api.schemas.sessions import SessionCreate

    with pytest.raises(Exception):
        SessionCreate(title="no prompt")
    with pytest.raises(Exception):
        SessionCreate(prompt="   ")
    assert SessionCreate(prompt="does bin width change AUC?").prompt


def test_session_update_cannot_carry_a_prompt():
    from application_api.schemas.sessions import SessionUpdate

    assert "prompt" not in SessionUpdate.model_fields


# ----------------------------------------------------------------------
# Step D — chats
# ----------------------------------------------------------------------
def test_chats_is_a_separate_table_from_sessions():
    tables = _metadata().tables
    assert "chats" in tables
    chat = tables["chats"]
    assert "session_id" in chat.columns
    # No `kind` — no distinct chat-kind behaviour exists.
    assert "kind" not in chat.columns
    # REJECTED removal: a dedicated TOUCH endpoint and eight frontend readers.
    assert "last_activity_at" in chat.columns


def test_chat_messages_hang_off_a_chat_not_a_session():
    fks = {
        fk.parent.name: str(fk.column)
        for fk in _metadata().tables["chat_messages"].foreign_keys
    }
    assert fks["chat_id"] == "chats.chat_id"


def test_migration_preserves_message_ids_and_refuses_to_lose_history():
    migration = _migration("d1f0a2c3d4e5")
    assert "message_ids preserved" in migration
    assert "will not delete message history" in migration
    # A row count that changes during the re-pointing is a failure, not a log
    # line.
    assert "chat_messages count changed during re-pointing" in migration


def test_chat_study_association_is_gone():
    assert "chat_study_association" not in _metadata().tables
    migration = _migration("d1f0a2c3d4e5")
    # Historical rows are counted and reported before the table goes.
    assert "SELECT count(*) FROM chat_study_association" in migration


# ----------------------------------------------------------------------
# Step E — the plan chain
# ----------------------------------------------------------------------
def test_hypotheses_table_does_not_exist():
    assert "hypotheses" not in _metadata().tables
    for name in ("hypothesis.py",):
        assert not (APPLICATION_ROOT / "db" / "models" / name).exists()
        assert not (APPLICATION_ROOT / "schemas" / name).exists()
        assert not (APPLICATION_ROOT / "repositories" / name).exists()
        assert not (APPLICATION_ROOT / "api" / "routes" / name).exists()


def test_plan_is_keyed_by_its_session():
    """Defect 2: `plans.hypothesis_id` NOT NULL UNIQUE meant no hypothesis, no
    plan — which forced every prompt to be a clinical hypothesis."""
    plans = _metadata().tables["session_graphs"]
    assert [c.name for c in plans.primary_key.columns] == ["session_id"]
    assert "hypothesis_id" not in plans.columns
    fks = {fk.parent.name: str(fk.column) for fk in plans.foreign_keys}
    assert fks["session_id"] == "sessions.session_id"


def test_plans_has_approved_at_and_no_version_or_goal_columns():
    columns = set(_metadata().tables["session_graphs"].columns.keys())
    assert "approved_at" in columns
    # //.
    for dead in ("state", "study_goal", "rationale", "created_at", "version", "plan_version"):
        assert dead not in columns


def test_pico_is_verified_row_for_row_before_hypotheses_is_dropped():
    migration = _migration("e1f0a2c3d4e5")
    assert "sessions.intent" in migration
    verify = migration.index("did not copy cleanly into")
    drop = migration.index("DROP TABLE hypotheses")
    assert verify < drop


def test_exactly_one_plan_per_session_is_enforced_by_the_database():
    migration = _migration("e1f0a2c3d4e5")
    assert "sessions_require_a_plan" in migration
    assert "CREATE CONSTRAINT TRIGGER sessions_must_have_a_plan" in migration
    assert "DEFERRABLE INITIALLY DEFERRED" in migration
    # And a plan cannot be deleted out from under its session.
    assert "plans_reject_orphan_delete" in migration


def test_approval_is_one_way_and_freezes_every_step():
    migration = _migration("e1f0a2c3d4e5")
    assert "plans_reject_unapproval" in migration
    assert "plan approval is one-way" in migration
    assert "plan_steps_reject_approved_mutation" in migration
    assert "its steps are immutable" in migration


def test_plan_steps_match_the_v1_shape():
    steps = _metadata().tables["graph_nodes"]
    columns = set(steps.columns.keys())
    assert {"node_id", "session_id", "position", "target_name", "title", "params"} <= columns
    # folded description into instructions; removed execution state.
    for dead in ("description", "status", "logs", "plan_id", "is_enabled", "created_at"):
        assert dead not in columns

    uniques = {
        tuple(c.name for c in constraint.columns): constraint
        for constraint in steps.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    reorder_key = uniques[("session_id", "position")]
    # DEFERRABLE so a reorder can swap two steps inside one transaction.
    assert reorder_key.deferrable is True
    # The supporting tuple agent_runs' composite FK targets in this migration.
    assert ("node_id", "session_id") in uniques


def test_agent_type_is_validated_against_the_agent_factories():
    """/: no catalog FK, but unknown keys are rejected before admission — never silently skipped."""
    from application_api.core.agent_types import CANONICAL_AGENT_TYPES

    execution_team = (AGENT_ROOT / "teams" / "execution_team.py").read_text(
        encoding="utf-8"
    )
    block = execution_team[
        execution_team.index("AGENT_FACTORIES = {") : execution_team.index("}", execution_team.index("AGENT_FACTORIES = {"))
    ]
    factory_keys = {
        line.split('"')[1] for line in block.splitlines() if line.strip().startswith('"')
    }
    assert factory_keys == set(CANONICAL_AGENT_TYPES), (
        "application_api/core/agent_types.py must mirror AGENT_FACTORIES; add "
        "the new agent to both."
    )

    plan_repo = (APPLICATION_ROOT / "repositories" / "session_graphs.py").read_text(encoding="utf-8")
    assert "unknown_agent_types" in plan_repo
    # 's lock, held through commit. Build this migration moved the locking into the
    # publication service so every writer takes session -> plan in that order
    # instead of each choosing its own.
    assert "lock_session_and_graph" in plan_repo
    publication = (
        APPLICATION_ROOT / "services" / "publication.py"
    ).read_text(encoding="utf-8")
    assert "FOR NO KEY UPDATE" in publication


def test_session_and_plan_are_created_in_one_transaction():
    repo = (APPLICATION_ROOT / "repositories" / "sessions.py").read_text(encoding="utf-8")
    create = repo[repo.index("async def create_session") : repo.index("async def get_session")]
    assert "SessionGraph(session_id=session.session_id" in create
    assert create.count("await db.commit()") == 1


# ----------------------------------------------------------------------
# The agent-system side of the cutover
# ----------------------------------------------------------------------
def test_agent_client_no_longer_discards_the_users_question():
    """Defect 1 on the agent side: the one field carrying the user's actual
    words was set to the empty string on every parse and documented as not
    persisted."""
    client = (AGENT_ROOT / "clients" / "backend_client.py").read_text(encoding="utf-8")
    assert 'question=session.get("prompt") or ""' in client
    assert "not persisted" not in client
    # The parser builds `question` from the session row; it no longer has a
    # hardcoded empty value.
    parser = client[
        client.index("def _parse_intent") : client.index("def _build_task_message_payload")
    ]
    assert 'question=""' not in parser


def test_agent_client_actually_writes_the_session():
    client = (AGENT_ROOT / "clients" / "backend_client.py").read_text(encoding="utf-8")
    update = client[client.index("async def update_session") : client.index("async def get_session")]
    # The whole body used to be commented out under "Session management not yet
    # implemented in DB".
    assert "client.patch(" in update
    assert "Session management not yet implemented" not in client
    # prompt is never sent: it is immutable.
    assert 'k != "prompt"' in update


def test_agent_boundary_rejects_non_uuid_session_ids():
    """A failed session create must be a visible error, never a local phantom
    run."""
    requests = (AGENT_ROOT / "models" / "requests.py").read_text(encoding="utf-8")
    assert "session_id: UUID" in requests

    client = (AGENT_ROOT / "clients" / "backend_client.py").read_text(encoding="utf-8")
    assert "_require_uuid" in client
    assert "Skipping ws log persistence for non-persisted session" not in client


def test_agent_client_has_no_hypothesis_endpoints_left():
    client = (AGENT_ROOT / "clients" / "backend_client.py").read_text(encoding="utf-8")
    for dead in (
        "/hypotheses/",
        "get_hypothesis_by_chat",
        "get_plan_by_hypothesis",
        "/chat_sessions/",
    ):
        assert dead not in client


# ----------------------------------------------------------------------
# The frontend side of the cutover
# ----------------------------------------------------------------------
FRONTEND = BACKEND_ROOT.parent / "Frontend" / "OHIF-Viewer"
MIGRATED_COMPONENTS = (
    FRONTEND / "extensions/research-playground/src/components/features/HomeDashboard.tsx",
    FRONTEND / "extensions/research-playground/src/components/PlaygroundCanvas/ChatSidebar.tsx",
    # platform/app/src/components/ChatWidget.tsx was deleted as dead code
    # (WEBSOCKET_FIX_PLAN.md DQ2/WS-03): it was never a live importer of any
    # chat_sessions/hypotheses endpoint by the time it was removed, and there
    # is now only one WebSocket client in the frontend.
)


@pytest.mark.parametrize("path", MIGRATED_COMPONENTS, ids=lambda p: p.name)
def test_frontend_uses_the_v1_session_and_chat_endpoints(path: Path):
    source = path.read_text(encoding="utf-8")
    assert "/chat_sessions/" not in source
    assert "/hypotheses/" not in source


def test_frontend_no_longer_mints_local_only_sessions():
    sidebar = (
        FRONTEND
        / "extensions/research-playground/src/components/PlaygroundCanvas/ChatSidebar.tsx"
    ).read_text(encoding="utf-8")
    assert "makeLocalSessionId" not in sidebar
    assert "local session, DB unavailable" not in sidebar
    assert "This chat is now local-only" not in sidebar
