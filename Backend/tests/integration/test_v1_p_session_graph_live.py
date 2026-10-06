"""Live checks of the session-graph migration (p1f0a2c3d4e5_v1_p_session_graph).

A synthetic o1 database -- approved and draft plans, runs in every legacy
status, duplicate chats -- is migrated to p1. The backfills are exact, the new rules hold for writes shaped like applying a
change, starting a node, completing and suspending an attempt, and
downgrade() either restores o1 or refuses.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

psycopg2 = pytest.importorskip("psycopg2", reason="psycopg2 is required for live checks")
from psycopg2.extras import Json  # noqa: E402

from application_api.core.canonical_json import content_digest  # noqa: E402

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "application_api" / "alembic.ini"
MIGRATION = (
    BACKEND_ROOT / "application_api" / "migrations" / "versions" / "p1f0a2c3d4e5_v1_p_session_graph.py"
)

LIVE_DSN = os.environ.get("V1_LIVE_DATABASE_URL")
CUTOVER_1 = "o1f0a2c3d4e5"
CUTOVER_2 = "p1f0a2c3d4e5"

pytestmark.append(
    pytest.mark.skipif(
        not LIVE_DSN,
        reason="set V1_LIVE_DATABASE_URL to a disposable PostgreSQL 18 database",
    )
)


# ----------------------------------------------------------------------
# Database helpers
# ----------------------------------------------------------------------
def _alembic(command: str, revision: str) -> subprocess.CompletedProcess:
    environment = dict(os.environ, DATABASE_URL=LIVE_DSN)
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), command, revision],
        cwd=str(BACKEND_ROOT),
        env=environment,
        capture_output=True,
        text=True,
    )


def _migrate(command: str, revision: str) -> None:
    completed = _alembic(command, revision)
    if completed.returncode != 0:
        raise AssertionError(f"alembic {command} {revision} failed:\n{completed.stderr}")


def _connect():
    connection = psycopg2.connect(LIVE_DSN)
    connection.autocommit = False
    return connection


def _fetch(connection, sql: str, params: tuple = ()) -> list[tuple]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    connection.rollback()
    return rows


def _scalar(connection, sql: str, params: tuple = ()) -> Any:
    rows = _fetch(connection, sql, params)
    return rows[0][0] if rows else None


@contextmanager
def _transaction(connection, path: str | None = None, **settings: str) -> Iterator[Any]:
    """One committed transaction on a declared write path."""
    with connection.cursor() as cursor:
        if path:
            cursor.execute("SELECT set_config('cubeai.actor', %s, true)", (path,))
        for name, value in settings.items():
            cursor.execute("SELECT set_config(%s, %s, true)", (f"cubeai.{name}", value))
        try:
            yield cursor
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _rejects(connection, statements: list[tuple[str, tuple]], path: str | None = None, **settings) -> str:
    """Run the statements in one transaction and return the database's refusal."""
    try:
        with _transaction(connection, path, **settings) as cursor:
            for sql, params in statements:
                cursor.execute(sql, params)
    except psycopg2.Error as exc:
        return str(exc)
    raise AssertionError(f"expected the database to refuse: {statements}")


def _reset_to_o1(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    connection.commit()
    _migrate("upgrade", CUTOVER_1)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_cubeai_v1_p_session_graph", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ----------------------------------------------------------------------
# An o1 database with every legacy shape the backfill must handle
# ----------------------------------------------------------------------
class O1Seed:
    """Writes legacy rows in the o1 vocabulary, inside one transaction."""

    def __init__(self, cursor) -> None:
        self.cursor = cursor

    def session(self, control_state: str) -> str:
        self.cursor.execute(
            "INSERT INTO sessions (title, prompt, prompt_kind, control_state) "
            "VALUES ('fixture', 'synthetic question', 'general', %s) RETURNING session_id",
            (control_state,),
        )
        session_id = str(self.cursor.fetchone()[0])
        self.cursor.execute(
            "INSERT INTO session_graphs (session_id, approved_at, updated_at) "
            "VALUES (%s, NULL, now())",
            (session_id,),
        )
        return session_id

    def nodes(self, session_id: str, *targets: str) -> list[str]:
        node_ids = []
        for position, target in enumerate(targets, start=1):
            self.cursor.execute(
                "INSERT INTO graph_nodes (session_id, position, target_name, title, params) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING node_id",
                (session_id, position, target, target, Json({"task": target, "weight": 0.25})),
            )
            node_ids.append(str(self.cursor.fetchone()[0]))
        return node_ids

    def approve(self, session_id: str, node_ids: list[str]) -> list[str]:
        """o1 approval: the plan and one pending run per node, together."""
        self.cursor.execute(
            "UPDATE session_graphs SET approved_at = now() WHERE session_id = %s", (session_id,)
        )
        runs = []
        for node_id in node_ids:
            self.cursor.execute(
                "INSERT INTO node_runs (node_id, session_id) VALUES (%s, %s) RETURNING run_id",
                (node_id, session_id),
            )
            runs.append(str(self.cursor.fetchone()[0]))
        return runs

    def start(self, run_id: str) -> None:
        self.cursor.execute(
            "UPDATE node_runs SET status = 'running', started_at = now(), compiled_prompt = 'p', "
            "resolved_inputs = %s WHERE run_id = %s",
            (Json({"schema_version": 1}), run_id),
        )

    def complete(self, run_id: str) -> None:
        self.start(run_id)
        self.cursor.execute(
            "UPDATE node_runs SET status = 'completed', completed_at = now() WHERE run_id = %s",
            (run_id,),
        )

    def fail(self, run_id: str, error: dict) -> None:
        self.start(run_id)
        self.cursor.execute(
            "UPDATE node_runs SET status = 'failed', completed_at = now(), error = %s "
            "WHERE run_id = %s",
            (Json(error), run_id),
        )

    def cohort(self, run_id: str, key: str) -> str:
        self.cursor.execute(
            "INSERT INTO cohorts (created_by_run_id, cohort_key, criteria, label_definition) "
            "VALUES (%s, %s, %s, %s) RETURNING cohort_id",
            (run_id, key, Json({"schema_version": 1}),
             Json({"schema_version": 1, "kind": "binary", "field": "has_target_disease"})),
        )
        return str(self.cursor.fetchone()[0])

    def chat(self, session_id: str, created_at: str) -> str:
        self.cursor.execute(
            "INSERT INTO chats (session_id, created_at) VALUES (%s, %s) RETURNING chat_id",
            (session_id, created_at),
        )
        return str(self.cursor.fetchone()[0])

    def message(self, chat_id: str, text: str, created_at: str) -> str:
        self.cursor.execute(
            "INSERT INTO chat_messages (chat_id, role, text, created_at) "
            "VALUES (%s, 'user', %s, %s) RETURNING message_id",
            (chat_id, text, created_at),
        )
        return str(self.cursor.fetchone()[0])


def _seed(connection, *, draft: bool = True, conflicting_chats: bool = False) -> dict[str, Any]:
    ids: dict[str, Any] = {}
    with _transaction(connection) as cursor:
        seed = O1Seed(cursor)

        # A: an approved plan with runs in every legacy status.
        a = ids["a"] = seed.session("running")
        a_nodes = ids["a_nodes"] = seed.nodes(
            a, "study_population_planner_agent", "segmentation_agent",
            "result_reporter_agent", "latex_reporter_agent",
        )
        population, segmentation, reporter, latex = seed.approve(a, a_nodes)
        seed.complete(population)
        ids["a_cohort"] = seed.cohort(population, "population")
        seed.start(segmentation)
        seed.fail(reporter, {"message": "boom"})
        ids.update(a_population=population, a_segmentation=segmentation,
                   a_reporter=reporter, a_latex=latex)

        # B: a population run with two cohorts (no exact single-row mapping).
        b = ids["b"] = seed.session("pausing")
        b_population, b_reporter = seed.approve(
            b, seed.nodes(b, "study_population_planner_agent", "result_reporter_agent")
        )
        seed.complete(b_population)
        seed.cohort(b_population, "train")
        seed.cohort(b_population, "test")
        seed.complete(b_reporter)
        ids.update(b_population=b_population, b_reporter=b_reporter)

        # C: a draft plan, never approved.
        if draft:
            c = ids["c"] = seed.session("planning")
            ids["c_nodes"] = seed.nodes(c, "study_population_planner_agent", "segmentation_agent")

        # D: an empty duplicate chat beside the conversation; E: two empty chats.
        d = ids["d"] = seed.session("archived")
        seed.chat(d, "2026-01-01T00:00:00Z")
        d_chat = ids["d_chat"] = seed.chat(d, "2026-01-02T00:00:00Z")
        ids["d_messages"] = [
            seed.message(d_chat, text, created_at)
            for text, created_at in (
                ("third", "2026-01-02T00:03:00Z"),
                ("first", "2026-01-02T00:01:00Z"),
                ("second", "2026-01-02T00:02:00Z"),
            )
        ]
        e = ids["e"] = seed.session("ready")
        ids["e_oldest_chat"] = seed.chat(e, "2026-01-01T00:00:00Z")
        seed.chat(e, "2026-01-03T00:00:00Z")

        # F: every node completed; G: a failed run. downgrade() rebuilds both values.
        f = ids["f"] = seed.session("completed")
        (f_population,) = seed.approve(f, seed.nodes(f, "study_population_planner_agent"))
        seed.complete(f_population)
        g = ids["g"] = seed.session("failed")
        (g_population,) = seed.approve(g, seed.nodes(g, "study_population_planner_agent"))
        seed.fail(g_population, {"message": "no matching studies"})

        if conflicting_chats:
            for created_at in ("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"):
                seed.message(seed.chat(e, created_at), "kept", created_at)
    return ids


@pytest.fixture(scope="module")
def migrated():
    connection = _connect()
    _reset_to_o1(connection)
    ids = _seed(connection)
    _migrate("upgrade", CUTOVER_2)
    try:
        yield connection, ids
    finally:
        connection.close()


# ----------------------------------------------------------------------
# Backfills
# ----------------------------------------------------------------------
def test_every_session_has_the_legacy_owner_and_only_control_intent(migrated):
    connection, ids = migrated
    assert _fetch(connection, "SELECT username FROM users") == [("legacy",)]
    assert _scalar(
        connection,
        "SELECT count(*) FROM sessions s JOIN users u USING (user_id) WHERE u.username <> 'legacy'",
    ) == 0
    states = dict(_fetch(connection, "SELECT session_id::text, control_state FROM sessions"))
    assert states[ids["a"]] == "active"  # running
    assert states[ids["b"]] == "paused"  # pausing
    assert states[ids["c"]] == "active"  # planning
    assert states[ids["d"]] == "archived"
    assert states[ids["e"]] == "active"  # ready
    assert states[ids["f"]] == states[ids["g"]] == "active"  # completed, failed


def test_an_approved_plan_becomes_applied_change_one(migrated):
    connection, ids = migrated
    (change_id, status, base, applied, author, decided_by, key, content, digest), = _fetch(
        connection,
        "SELECT change_id::text, status, base_version, applied_version, author, decided_by, "
        "idempotency_key, content, content_digest FROM graph_changes WHERE session_id = %s",
        (ids["a"],),
    )
    assert (status, base, applied, author, decided_by, key) == (
        "applied", 0, 1, "legacy", "user", "initial-backfill"
    )
    assert digest == content_digest(content)
    assert [node["node_id"] for node in content["nodes"]] == ids["a_nodes"]
    assert content["nodes"][0]["params"] == {"task": "study_population_planner_agent", "weight": 0.25}
    assert _scalar(connection, "SELECT version FROM session_graphs WHERE session_id = %s", (ids["a"],)) == 1
    assert _fetch(
        connection,
        "SELECT node_seq = position, accepted_by_change_id::text, target_kind, target_version "
        "FROM graph_nodes WHERE session_id = %s GROUP BY 1, 2, 3, 4",
        (ids["a"],),
    ) == [(True, change_id, "agent", "legacy")]


def test_a_draft_becomes_a_pending_change_only_a_user_can_apply(migrated):
    connection, ids = migrated
    (status, reasons, content, decided_at), = _fetch(
        connection,
        "SELECT status, approval_reasons, content, decided_at FROM graph_changes "
        "WHERE session_id = %s",
        (ids["c"],),
    )
    assert (status, reasons, decided_at) == ("pending", ["approval_required:legacy_draft"], None)
    assert content["origin"] == "legacy_draft"
    assert [node["node_id"] for node in content["nodes"]] == ids["c_nodes"]
    assert _scalar(connection, "SELECT count(*) FROM graph_nodes WHERE session_id = %s", (ids["c"],)) == 0
    assert _scalar(connection, "SELECT version FROM session_graphs WHERE session_id = %s", (ids["c"],)) == 0
    assert "ck_graph_changes_reasons_need_a_user" in _rejects(connection, [(
        "UPDATE graph_changes SET status = 'applied', decided_by = 'auto', decided_at = now(), "
        "applied_version = 1 WHERE session_id = %s",
        (ids["c"],),
    )])


def test_outputs_are_backfilled_only_from_an_exact_mapping(migrated):
    connection, ids = migrated
    outputs = dict(_fetch(connection, "SELECT run_id::text, outputs FROM node_runs"))
    assert outputs[ids["a_population"]] == {
        "v": 1,
        "origin": "legacy_backfill",
        "ports": {"cohort": {
            "type": "Cohort",
            "ref": {"table": "cohorts", "ids": [ids["a_cohort"]]},
            "summary": None,
            "phi": True,
        }},
    }
    assert outputs[ids["b_population"]] is None  # two cohorts: no single-row ref
    assert outputs[ids["b_reporter"]]["ports"]["summary"]["ref"] == {
        "table": "node_runs", "ids": [ids["b_reporter"]]
    }
    assert outputs[ids["a_segmentation"]] is None


def test_runs_become_projections_of_their_provable_attempts(migrated):
    connection, ids = migrated
    runs = {
        run: (status, error, superseded)
        for run, status, error, superseded in _fetch(
            connection,
            "SELECT run_id::text, status, error, superseded_at IS NOT NULL FROM node_runs",
        )
    }
    attempts = {
        run: (state, kind, number, error)
        for run, state, kind, number, error in _fetch(
            connection,
            "SELECT run_id::text, state, dispatch_kind, attempt_number, error FROM run_attempts",
        )
    }
    assert runs[ids["a_population"]] == ("completed", None, False)
    assert attempts[ids["a_population"]] == ("succeeded", "legacy", 1, None)

    interrupted = {"code": "interrupted", "source": "migration", "legacy_error": None}
    assert runs[ids["a_segmentation"]] == ("failed", interrupted, False)
    assert attempts[ids["a_segmentation"]] == ("interrupted", "legacy", 1, interrupted)

    wrapped = {"code": "error", "source": "migration", "legacy_error": {"message": "boom"}}
    assert runs[ids["a_reporter"]] == ("failed", wrapped, False)
    assert attempts[ids["a_reporter"]][0::3] == ("failed", wrapped)

    assert runs[ids["a_latex"]] == (
        "cancelled", {"code": "legacy_not_started", "source": "migration"}, True
    )
    assert ids["a_latex"] not in attempts


def test_chats_are_one_per_session_and_the_transcript_has_a_total_order(migrated):
    connection, ids = migrated
    chats = _fetch(connection, "SELECT session_id::text, chat_id::text FROM chats")
    assert sorted(chat for session, chat in chats if session in (ids["d"], ids["e"])) == sorted(
        [ids["d_chat"], ids["e_oldest_chat"]]
    )
    assert [text for (text,) in _fetch(
        connection, "SELECT text FROM chat_messages WHERE chat_id = %s ORDER BY seq", (ids["d_chat"],)
    )] == ["first", "second", "third"]
    assert "uq_chats_session_id" in _rejects(
        connection, [("INSERT INTO chats (session_id) VALUES (%s)", (ids["d"],))]
    )


# ----------------------------------------------------------------------
# Rules
# ----------------------------------------------------------------------
def test_the_fencing_trigger_guards_every_table_execution_writes(migrated):
    connection, ids = migrated
    fenced = set(_load_migration().FENCED_TABLES)
    attached = {
        table for (table,) in _fetch(
            connection,
            "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE t.tgname = c.relname || '_fencing' AND NOT t.tgisinternal",
        )
    }
    assert attached == fenced
    with_run_column = {
        table for (table,) in _fetch(
            connection,
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'created_by_run_id'",
        )
    }
    assert with_run_column <= fenced

    assert "declares no write path" in _rejects(connection, [(
        "INSERT INTO stored_files (session_id, created_by_run_id, kind, logical_key, "
        "display_filename, mime_type, byte_size, sha256, body, retention_class) VALUES "
        "(%s, %s, 'export_csv', 'x.csv', 'x.csv', 'text/csv', 3, %s, 'a,b'::bytea, 'retained')",
        (ids["a"], ids["a_population"], "a" * 64),
    )])


def test_graph_nodes_change_only_with_the_graph_version(migrated):
    connection, ids = migrated
    assert "advances the graph version" in _rejects(
        connection,
        [("UPDATE graph_nodes SET title = 'renamed' WHERE session_id = %s", (ids["a"],))],
        "harness",
    )
    assert "never deleted" in _rejects(
        connection, [("DELETE FROM graph_nodes WHERE session_id = %s", (ids["a"],))], "harness"
    )
    assert "legacy runs come only from the migration backfill" in _rejects(connection, [(
        "INSERT INTO node_runs (node_id, session_id, status, started_at, compiled_prompt, "
        "resolved_inputs, accepted_by_change_id, execution_policy, target_version) "
        "SELECT node_id, session_id, 'running', now(), 'p', '{}'::jsonb, accepted_by_change_id, "
        "jsonb_build_object('v', 1, 'legacy', true), 'legacy' FROM graph_nodes WHERE node_id = %s",
        (ids["a_nodes"][0],),
    )], "harness")


def _apply_change(connection, session_id: str, base_version: int, nodes: list[dict]) -> str:
    """An auto-applied change in the migration's order: the change and its nodes
    first, the version advance last (checked at commit)."""
    content = {"v": 1, "nodes": nodes, "rerun_node_ids": [], "release_hold_node_ids": [],
               "on_running": "reject"}
    with _transaction(connection, "harness") as cursor:
        cursor.execute(
            "INSERT INTO graph_changes (session_id, author, idempotency_key, base_version, content, "
            "content_digest, status, decided_by, decided_at, applied_version) VALUES "
            "(%s, 'harness', %s, %s, %s, %s, 'applied', 'auto', now(), %s) RETURNING change_id",
            (session_id, str(uuid.uuid4()), base_version, Json(content), content_digest(content),
             base_version + 1),
        )
        change_id = str(cursor.fetchone()[0])
        for node in nodes:
            cursor.execute(
                "INSERT INTO graph_nodes (node_id, session_id, node_seq, position, target_kind, "
                "target_name, target_version, title, params, accepted_by_change_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, '1', %s, '{}'::jsonb, %s)",
                (node["node_id"], session_id, node["seq"], node["seq"], node["kind"], node["ref"],
                 node["ref"], change_id),
            )
        cursor.execute(
            "UPDATE session_graphs SET version = version + 1 WHERE session_id = %s", (session_id,)
        )
    return change_id


_START_RUN = (
    "INSERT INTO node_runs (run_id, node_id, session_id, status, started_at, compiled_prompt, "
    "resolved_inputs, accepted_by_change_id, execution_policy, target_version) "
    "VALUES (%s, %s, %s, 'running', now(), %s, '{\"v\": 1}'::jsonb, %s, "
    "'{\"v\": 1, \"max_attempts\": 2, \"resumable\": true}'::jsonb, '1')"
)
_OPEN_ATTEMPT = (
    "INSERT INTO run_attempts (attempt_id, run_id, session_id, attempt_number, dispatch_kind, state) "
    "VALUES (%s, %s, %s, %s, %s, 'preparing')"
)


def test_a_node_runs_through_the_harness_transactions(migrated):
    """Apply -> start -> worker -> checkpoint -> suspend -> complete, with each refusal in between."""
    connection, ids = migrated
    session = ids["a"]
    agent_node, tool_node = str(uuid.uuid4()), str(uuid.uuid4())
    change = _apply_change(connection, session, 1, [
        {"node_id": agent_node, "seq": 5, "kind": "agent", "ref": "segmentation_agent"},
        {"node_id": tool_node, "seq": 6, "kind": "tool", "ref": "latex_to_pdf"},
    ])

    # Start: the agent node starts with its run and attempt 1.
    run, attempt = str(uuid.uuid4()), str(uuid.uuid4())
    with _transaction(connection, "harness") as cursor:
        cursor.execute(_START_RUN, (run, agent_node, session, "prompt", change))
        cursor.execute(_OPEN_ATTEMPT, (attempt, run, session, 1, "in_process"))

    tool_run = str(uuid.uuid4())
    assert "uq_run_attempts_one_active_per_session" in _rejects(connection, [
        (_START_RUN, (tool_run, tool_node, session, None, change)),
        (_OPEN_ATTEMPT, (str(uuid.uuid4()), tool_run, session, 1, "inline")),
    ], "harness")
    assert "a tool node without one" in _rejects(
        connection, [(_START_RUN, (tool_run, tool_node, session, "prompt", change))], "harness"
    )
    assert "has no attempt" in _rejects(
        connection, [(_START_RUN, (tool_run, tool_node, session, None, change))], "harness"
    )

    file_row = (
        "INSERT INTO stored_files (session_id, created_by_run_id, kind, logical_key, "
        "display_filename, mime_type, byte_size, sha256, body, retention_class) VALUES "
        "(%s, %s, 'export_csv', %s, 'x.csv', 'text/csv', 3, %s, 'a,b'::bytea, 'retained')"
    )
    assert "only a submitted or running attempt writes" in _rejects(
        connection, [(file_row, (session, run, "early.csv", "b" * 64))], attempt_id=attempt
    )

    # The worker marks its own attempt running, then writes on the attempt path.
    with _transaction(connection, attempt_id=attempt) as cursor:
        cursor.execute(
            "UPDATE run_attempts SET state = 'running', dispatched_at = now(), started_at = now() "
            "WHERE attempt_id = %s",
            (attempt,),
        )
    with _transaction(connection, attempt_id=attempt) as cursor:
        cursor.execute(file_row, (session, run, "result.csv", "c" * 64))
    # Any run may store a file, but only a population node creates a cohort.
    assert "rows are owned by" in _rejects(connection, [(
        "INSERT INTO cohorts (created_by_run_id, cohort_key, criteria, label_definition) "
        "VALUES (%s, 'stray', %s, %s)",
        (run, Json({"schema_version": 1}),
         Json({"schema_version": 1, "kind": "binary", "field": "has_target_disease"})),
    )], attempt_id=attempt)
    legacy_attempt = _scalar(
        connection, "SELECT attempt_id::text FROM run_attempts WHERE run_id = %s", (ids["a_population"],)
    )
    assert "cannot write rows of run" in _rejects(
        connection, [(file_row, (session, run, "stale.csv", "d" * 64))], attempt_id=legacy_attempt
    )

    # Checkpoints: written by the attempt; a replayed pending write is a no-op.
    with _transaction(connection, attempt_id=attempt) as cursor:
        cursor.execute(
            "INSERT INTO agent_checkpoints (run_id, session_id, checkpoint_id, attempt_id, type, "
            "checkpoint, metadata) VALUES (%s, %s, 'c1', %s, 'msgpack', '\\x00'::bytea, %s)",
            (run, session, attempt, Json({"v": 1, "spec_digest": "s", "engine_version": "1"})),
        )
        for _ in range(2):
            cursor.execute(
                "INSERT INTO agent_checkpoint_writes (run_id, session_id, checkpoint_id, task_id, "
                "idx, channel, type, value, attempt_id) "
                "VALUES (%s, %s, 'c1', 't1', 0, 'messages', 'msgpack', '\\x01'::bytea, %s)",
                (run, session, attempt),
            )
    assert _scalar(
        connection, "SELECT count(*) FROM agent_checkpoint_writes WHERE run_id = %s", (run,)
    ) == 1

    # Suspend after the interrupt checkpoint; resume only after the decision.
    def suspension(checkpoint_id: str) -> Json:
        return Json({"v": 1, "kind": "gate", "gate": "tool_approval", "interrupt_id": "i1",
                     "checkpoint_id": checkpoint_id})

    suspend = "UPDATE run_attempts SET state = 'suspended', suspension = %s WHERE attempt_id = %s"
    resume = "UPDATE run_attempts SET state = 'running', resumed_at = now() WHERE attempt_id = %s"
    assert "after storing its interrupt checkpoint" in _rejects(
        connection, [(suspend, (suspension("missing"), attempt))], "harness"
    )
    with _transaction(connection, "harness") as cursor:
        cursor.execute(suspend, (suspension("c1"), attempt))
    assert "after its gate is decided" in _rejects(connection, [(resume, (attempt,))], "harness")
    with _transaction(connection, "user") as cursor:
        cursor.execute(
            "UPDATE run_attempts SET suspension = suspension || %s WHERE attempt_id = %s",
            (Json({"resume": {"decision": "approve"}}), attempt),
        )
    with _transaction(connection, "harness") as cursor:
        cursor.execute(resume, (attempt,))

    # Complete: the attempt succeeds and the run completes with its outputs, together.
    assert "projects" in _rejects(connection, [(
        "UPDATE run_attempts SET state = 'succeeded', ended_at = now() WHERE attempt_id = %s",
        (attempt,),
    )], "harness")
    with _transaction(connection, "harness") as cursor:
        cursor.execute(
            "UPDATE run_attempts SET state = 'succeeded', ended_at = now() WHERE attempt_id = %s",
            (attempt,),
        )
        cursor.execute(
            "UPDATE node_runs SET status = 'completed', completed_at = now(), "
            "outputs = '{\"v\": 1, \"ports\": {}}'::jsonb WHERE run_id = %s",
            (run,),
        )
    assert "only a submitted or running attempt writes" in _rejects(
        connection, [(file_row, (session, run, "late.csv", "e" * 64))], attempt_id=attempt
    )
    # The row rule holds even on the one path that skips fencing.
    assert "is completed; only its supersession and hold" in _rejects(connection, [(
        "UPDATE node_runs SET scratchpad_summary = '{}'::jsonb WHERE run_id = %s", (run,)
    )], "migration")


def test_a_tool_result_answers_an_earlier_call_of_its_turn(migrated):
    connection, ids = migrated
    chat = ids["d_chat"]
    calls = Json({"v": 2, "kind": "tool_calls", "turn_id": 7, "calls": [{"call_id": "c1"}]})
    result = Json({"v": 2, "kind": "tool_result", "turn_id": 7, "call_id": "c1", "status": "ok"})
    insert = "INSERT INTO chat_messages (chat_id, role, text, metadata) VALUES (%s, %s, %s, %s)"
    assert "which no earlier tool_calls row" in _rejects(
        connection, [(insert, (chat, "tool", "count_studies", result))]
    )
    assert "uq_chat_messages_one_tool_result" in _rejects(connection, [
        (insert, (chat, "assistant", "1 call", calls)),
        (insert, (chat, "tool", "count_studies", result)),
        (insert, (chat, "tool", "count_studies", result)),
    ])


def test_downgrade_refuses_once_the_harness_has_written(migrated):
    completed = _alembic("downgrade", CUTOVER_1)
    assert completed.returncode != 0
    assert "Refusing to downgrade the session-graph migration" in completed.stderr
    assert "a run was started after the migration" in completed.stderr


# ----------------------------------------------------------------------
# Whole-migration outcomes (each resets the database)
# ----------------------------------------------------------------------
def test_a_session_with_two_conversations_stops_the_migration():
    connection = _connect()
    try:
        _reset_to_o1(connection)
        _seed(connection, conflicting_chats=True)
        completed = _alembic("upgrade", CUTOVER_2)
        assert completed.returncode != 0
        assert "more than one chat with messages" in completed.stderr
        assert _scalar(connection, "SELECT version_num FROM alembic_version") == CUTOVER_1
    finally:
        connection.close()


def test_downgrade_restores_the_o1_rows_when_nothing_new_was_made():
    connection = _connect()
    try:
        _reset_to_o1(connection)
        ids = _seed(connection, draft=False)
        _migrate("upgrade", CUTOVER_2)
        _migrate("downgrade", CUTOVER_1)

        runs = {
            run: (status, error)
            for run, status, error in _fetch(
                connection, "SELECT run_id::text, status, error FROM node_runs"
            )
        }
        assert runs[ids["a_segmentation"]] == ("running", None)
        assert runs[ids["a_reporter"]] == ("failed", {"message": "boom"})
        assert runs[ids["a_latex"]] == ("pending", None)
        assert runs[ids["a_population"]] == ("completed", None)
        assert _scalar(connection, "SELECT to_regclass('public.users')") is None
        # The o1 progress value is rebuilt from the runs; intent is kept as is.
        states = dict(_fetch(connection, "SELECT session_id::text, control_state FROM sessions"))
        assert [states[ids[key]] for key in "abdefg"] == [
            "running", "paused", "archived", "ready", "completed", "failed"
        ]
        assert _scalar(connection, "SELECT count(*) FROM chat_messages") == 3
    finally:
        _migrate("upgrade", CUTOVER_2)
        connection.close()
