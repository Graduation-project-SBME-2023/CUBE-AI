"""Contract tests for the current execution spine."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APPLICATION_ROOT = BACKEND_ROOT / "application_api"
AGENT_ROOT = BACKEND_ROOT / "agent_system"
TOOLPACKS_ROOT = BACKEND_ROOT / "agent_system" / "toolpacks"
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


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _agent_http_source() -> str:
    """The Agent System HTTP layer: app.py plus the api/ package it was split into."""
    paths = [AGENT_ROOT / "app.py", *sorted((AGENT_ROOT / "api").rglob("*.py"))]
    return "\n".join(_source(path) for path in paths)


# ----------------------------------------------------------------------
# agent_runs — execution state only
# ----------------------------------------------------------------------
def test_node_runs_has_the_execution_shape():
    table = _metadata().tables["node_runs"]
    assert set(table.columns.keys()) == {
        "run_id", "node_id", "session_id", "accepted_by_change_id", "execution_policy",
        "target_version", "compiled_prompt", "resolved_inputs", "status", "superseded_at",
        "outputs", "downstream_hold", "hold_released_by_change_id", "scratchpad_notes",
        "scratchpad_warnings", "scratchpad_summary", "error", "started_at", "completed_at",
    }
    assert "run_attempts" in _metadata().tables


def test_agent_runs_keeps_no_copy_of_the_step_definition():
    """removed the snapshots; `plan_steps` is the sole definition."""
    columns = set(_metadata().tables["node_runs"].columns.keys())
    for removed in (
        "attempt_number",
        "agent_type_snapshot",
        "step_order_snapshot",
        "title_snapshot",
        "instructions_snapshot",
        "input_snapshot",
        "plan_version",
        # Approval time is plans.approved_at; started_at is the execution.
        "created_at",
        "updated_at",
    ):
        assert removed not in columns


def test_one_current_run_per_node_is_a_schema_fact():
    """A node has many runs over time and at most one current."""
    table = _metadata().tables["node_runs"]
    indexes = {index.name: index for index in table.indexes}
    current = indexes["uq_node_runs_one_current"]
    assert current.unique and [c.name for c in current.columns] == ["node_id"]
    assert "superseded_at IS NULL" in str(current.dialect_options["postgresql"]["where"])
    uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("run_id", "session_id") in uniques
    assert ("node_id",) not in uniques


def test_session_id_is_guarded_by_a_composite_fk_to_the_step():
    """The one deliberate copy of a `plan_steps` column, proven by FK."""
    table = _metadata().tables["node_runs"]
    composite = [
        constraint
        for constraint in table.foreign_key_constraints
        if {column.name for column in constraint.columns} == {"node_id", "session_id"}
    ]
    assert composite, "agent_runs needs the (node_id, session_id) composite FK"
    assert all(
        foreign_key.ondelete == "RESTRICT"
        for constraint in table.foreign_key_constraints
        for foreign_key in constraint.elements
    )


def test_run_status_and_start_rules_are_named_checks():
    checks = {
        constraint.name
        for constraint in _metadata().tables["node_runs"].constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert {
        "ck_node_runs_status",
        "ck_node_runs_completed_at_iff_terminal",
        "ck_node_runs_started_unless_never_started",
        "ck_node_runs_inputs_before_start",
        "ck_node_runs_failed_has_error",
        "ck_node_runs_downstream_hold",
        "ck_node_runs_outputs_only_when_completed",
        "ck_node_runs_json_bounded",
    } <= checks


def test_no_jobs_table_exists():
    """MONAI's on-disk receipt plus the domain natural keys cover current."""
    assert "jobs" not in _metadata().tables


# ----------------------------------------------------------------------
# — approval creates exactly one run per step, atomically
# ----------------------------------------------------------------------
def test_an_applied_change_creates_no_run():
    """Runs are created lazily, when the node starts, together with their attempt 1."""
    changes = _source(APPLICATION_ROOT / "repositories" / "graph_changes.py")
    assert "NodeRun(" not in changes and "RunAttempt(" not in changes
    runs = _source(APPLICATION_ROOT / "repositories" / "node_runs.py")
    first_start = runs[runs.index("async def _first_start") : runs.index("async def _retry")]
    assert "NodeRun(" in first_start and "_attempt(run, 1," in first_start


def test_the_deferred_trigger_checks_exact_cardinality():
    migration = _migration("f1f0a2c3d4e5")
    assert "plans_enforce_run_cardinality" in migration
    assert "DEFERRABLE INITIALLY DEFERRED" in migration
    for trigger in (
        '("plans_run_cardinality", "plans"',
        '("plan_steps_run_cardinality", "plan_steps"',
        '("agent_runs_run_cardinality", "agent_runs"',
    ):
        assert trigger in migration
    # A draft has none; an approved plan has one per step, with no extras.
    assert "draft plan for session" in migration
    assert "exactly one run per step" in migration


def test_the_migration_creates_no_run_that_claims_to_have_executed():
    """Never fabricate a run. Legacy approved plans get *pending* rows."""
    migration = _migration("f1f0a2c3d4e5")
    insert = migration[migration.index("INSERT INTO agent_runs") : migration.index("admitted = _count")]
    assert "'pending'" in insert
    assert "started_at" not in insert
    assert "compiled_prompt" not in insert
    assert "none claims to have executed" in migration


def test_a_run_is_never_deleted_and_a_completed_one_is_frozen():
    migration = _migration("f1f0a2c3d4e5")
    assert "agent_runs_enforce_row_rules" in migration
    assert "cannot be deleted" in migration
    assert "completed and immutable" in migration
    assert "immutable once" in migration  # compiled_prompt / resolved_inputs


# ----------------------------------------------------------------------
# — the legacy side tables are folded in, then dropped
# ----------------------------------------------------------------------
def test_task_messages_and_context_augmentations_are_gone():
    tables = set(_metadata().tables)
    assert "task_messages" not in tables
    assert "context_augmentations" not in tables

    for path in (
        APPLICATION_ROOT / "db" / "models" / "Taskmessage.py",
        APPLICATION_ROOT / "db" / "models" / "contextaugmentation.py",
        APPLICATION_ROOT / "repositories" / "Taskmessage.py",
        APPLICATION_ROOT / "api" / "routes" / "Taskmessage.py",
        APPLICATION_ROOT / "schemas" / "Taskmessage.py",
    ):
        assert not path.exists(), f"{path.name} must be removed in build step F"


def test_the_compiled_task_message_is_folded_into_the_step_definition():
    migration = _migration("f1f0a2c3d4e5")
    assert "'task_message', jsonb_build_object" in migration.replace("{{", "{")
    # Reconciled row-for-row before the table is dropped.
    assert "fold into plan_steps.instructions.task_message" in migration
    assert "DROP TABLE task_messages" in migration

    client = _source(AGENT_ROOT / "clients" / "backend_client.py")
    assert '"task_message"' in client
    assert "save_task_message_by_step_id" not in client


# ----------------------------------------------------------------------
# ws_log — the replay cursor and
# ----------------------------------------------------------------------
def test_ws_log_is_keyed_by_a_global_event_id():
    table = _metadata().tables["session_events"]
    assert "event_id" in table.columns
    assert "uuid" not in table.columns
    assert table.columns["event_id"].primary_key
    assert table.columns["event_type"].nullable is False
    assert table.columns["payload"].nullable is False


def test_the_identity_caches_one_value():
    migration = _migration("f1f0a2c3d4e5")
    assert "GENERATED ALWAYS AS IDENTITY (CACHE 1)" in migration


def test_every_publisher_locks_the_session_before_allocating_an_event_id():
    publication = _source(APPLICATION_ROOT / "services" / "publication.py")
    assert "FOR NO KEY UPDATE" in publication
    assert "lock_session" in publication
    # The point of the lock, stated where the code is.
    assert "commit order" in publication

    repo = _source(APPLICATION_ROOT / "repositories" / "session_events.py")
    assert "publication(" in repo
    # An event log has no update, no payload append and no delete.
    for removed in ("async def update_ws_log", "async def append_ws_payload", "async def delete_ws_log"):
        assert removed not in repo


def test_replay_reads_from_a_cursor_not_from_the_whole_log():
    repo = _source(APPLICATION_ROOT / "repositories" / "session_events.py")
    assert "after_event_id" in repo
    assert "SessionEvent.event_id > after_event_id" in repo

    app = _agent_http_source()
    assert "after_event_id" in app


def test_a_transition_broadcasts_the_event_it_already_committed():
    service = _source(AGENT_ROOT / "services" / "session_service.py")
    assert "_broadcast_committed_event" in service
    socket_store = _source(AGENT_ROOT / "socket_store.py")
    # Persist first, then broadcast, and never silently drop.
    assert "event_id" in socket_store
    assert "will not be" in socket_store  # the "not replayable" error path


# ----------------------------------------------------------------------
# — identity is the step and its run
# ----------------------------------------------------------------------
def test_the_runner_builds_one_node_per_run():
    team = _source(AGENT_ROOT / "teams" / "execution_team.py")
    assert "PlannedRun" in team
    assert "RunBoundAgent" in team
    assert "start_node_id" in team
    # The legacy de-duplication by agent id is gone.
    assert "planned_ids" not in team
    assert "start_agent_id" not in team


def test_each_node_claims_its_own_run_before_the_step_starts():
    wrapper = _source(AGENT_ROOT / "agents" / "run_bound_agent.py")
    assert "on_messages_stream" in wrapper
    assert "await self._on_start(self._run)" in wrapper
    # A cancelled step is interrupted, not failed.
    assert "leaving the run open" in wrapper


def test_the_mcp_environment_carries_the_run_identity():
    for name in (
        "population_agent",
        "segmentation_agent",
        "feature_extraction_agent",
        "modeling_agent",
        "result_reporter_agent",
        "reporter_agent",
    ):
        source = _source(AGENT_ROOT / "agents" / "execution_agents" / f"{name}.py")
        assert '"RUN_ID"' in source, name
        assert '"NODE_ID"' in source, name
        assert "node_name" in source, name


# ----------------------------------------------------------------------
# — two files that stop being files
# ----------------------------------------------------------------------
def test_the_scratchpad_is_the_run_not_a_json_file():
    source = _source(TOOLPACKS_ROOT / "bookkeeping" / "scratchpad.py")
    assert "SCRATCHPAD_FILE" not in source
    assert "/runs/" in source
    assert "run_id()" in source  # the run comes from the tool context
    # The MCP signatures are unchanged so the agents' prompts keep working.
    for tool in (
        "scratchpad_register_agent",
        "scratchpad_write_note",
        "scratchpad_raise_flag",
        "scratchpad_set_status",
        "scratchpad_write_summary",
        "scratchpad_read_agent",
        "scratchpad_read_report",
    ):
        assert f"async def {tool}(" in source


def test_agent_output_is_the_run_not_a_json_file():
    source = _source(TOOLPACKS_ROOT / "bookkeeping" / "agent_output.py")
    assert "OUTPUTS_DIR" not in source
    # No file is opened or written; the only mention left is the docstring
    # that says where the document used to live.
    assert "open(" not in source
    assert "write_text" not in source
    assert "/runs/" in source

    # The in-process writer (the segmentation tool) was cut over too, with no
    # file fallback.
    segmentation = _source(AGENT_ROOT / "agents" / "execution_agents" / "segmentation_agent.py")
    assert 'Path(outputs_dir) / "segmentation_agent_output.json"' not in segmentation
    assert "output_path.write_text" not in segmentation
    assert "write_agent_output" in segmentation
    assert "does not fall back to a workspace file" in segmentation


def test_task_context_reads_the_run(monkeypatch):
    source = _source(TOOLPACKS_ROOT / "bookkeeping" / "task_context.py")
    # The run row, through the Application API (domain_api names the /runs/ routes).
    assert "domain_api.get_run()" in source and "domain_api.run_by_target(" in source
    assert "compiled_prompt" in source
    assert "/task-messages/" not in source
    assert "steps_sharing_this_agent_type" in source


# ----------------------------------------------------------------------
# — recovery deletes nothing
# ----------------------------------------------------------------------
def test_recovery_has_no_delete_path_left():
    source = _source(AGENT_ROOT / "services" / "resume_recovery.py")
    assert "shutil.rmtree" not in source
    assert "def reset_interrupted_agent" not in source
    assert "AGENT_ARTIFACTS" not in source
    # Insert-or-read AND COMPARE: DO NOTHING alone is not idempotency.
    assert "reconcile_replayed_payload" in source
    assert "RecoveryConflict" in source


def test_resume_continues_the_same_run_without_clearing_artifacts():
    service = _source(AGENT_ROOT / "services" / "session_service.py")
    resume = service[service.index("async def resume_pipeline") : service.index("def pause(")]
    assert "reset_interrupted_agent(" not in resume
    assert '"cleared_artifacts": []' in resume
    assert "first_incomplete" in resume


def test_restart_cannot_re_run_a_step():
    service = _source(AGENT_ROOT / "services" / "session_service.py")
    start = service[service.index("async def start_execution") : service.index("async def resume_pipeline")]
    assert "requires a new session" in start
    assert "approve_plan" in start
