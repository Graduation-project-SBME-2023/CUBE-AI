"""Live checks of Cut-over 1 (o1f0a2c3d4e5_v1_o_renames, plan.md Phase 6.1).

A synthetic database at the last V1 revision is migrated to o1 and back:
renames only, data preserved, the two value rewrites applied and reversed,
and the re-created trigger functions still enforce their rules under the new
names.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

psycopg2 = pytest.importorskip("psycopg2", reason="psycopg2 is required for live checks")

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "application_api" / "alembic.ini"

LIVE_DSN = os.environ.get("V1_LIVE_DATABASE_URL")
PRE_CUTOVER = "n1f0a2c3d4e5"
CUTOVER_1 = "o1f0a2c3d4e5"

pytestmark.append(
    pytest.mark.skipif(
        not LIVE_DSN,
        reason="set V1_LIVE_DATABASE_URL to a disposable PostgreSQL 18 database",
    )
)

OLD_TABLES = (
    "plans", "plan_steps", "agent_runs", "ws_log",
    "artifacts", "segmentation_runs", "segmentations", "models",
)
NEW_TABLES = (
    "session_graphs", "graph_nodes", "node_runs", "session_events",
    "stored_files", "segmentation_batches", "segmentation_masks", "inference_models",
)


def _alembic(command: str, revision: str) -> None:
    environment = dict(os.environ)
    environment["DATABASE_URL"] = LIVE_DSN
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), command, revision],
        cwd=str(BACKEND_ROOT),
        env=environment,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"alembic {command} {revision} failed:\n{completed.stdout}\n{completed.stderr}"
        )


def _connect():
    connection = psycopg2.connect(LIVE_DSN)
    connection.autocommit = False
    return connection


def _scalar(connection, sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    connection.rollback()
    return row[0] if row else None


def _rejects(connection, sql, params=()) -> str:
    with connection.cursor() as cursor:
        try:
            cursor.execute(sql, params)
        except psycopg2.Error as exc:
            connection.rollback()
            return str(exc)
    connection.rollback()
    raise AssertionError(f"expected the database to refuse: {sql}")


def _tables(connection) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        rows = {row[0] for row in cursor.fetchall()}
    connection.rollback()
    return rows


SESSION = str(uuid.uuid4())
POPULATION_STEP = str(uuid.uuid4())
SEGMENTATION_STEP = str(uuid.uuid4())


def _seed_n1(connection) -> dict[str, str]:
    """A small pre-cut-over database, written with the n1 names."""
    ids: dict[str, str] = {}
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO sessions (session_id, title, prompt, prompt_kind, status) "
            "VALUES (%s, 'fixture', 'synthetic prompt', 'general', 'ready')",
            (SESSION,),
        )
        cursor.execute(
            "INSERT INTO plans (session_id, approved_at, updated_at) VALUES (%s, NULL, now())",
            (SESSION,),
        )
        for order, (step_id, agent) in enumerate(
            ((POPULATION_STEP, "study_population_planner_agent"),
             (SEGMENTATION_STEP, "segmentation_agent")),
            start=1,
        ):
            cursor.execute(
                "INSERT INTO plan_steps (step_id, session_id, step_order, agent_type, title, "
                "instructions) VALUES (%s, %s, %s, %s, %s, '{}'::jsonb)",
                (step_id, SESSION, order, agent, agent),
            )
    connection.commit()
    with connection.cursor() as cursor:
        cursor.execute("UPDATE plans SET approved_at = now() WHERE session_id = %s", (SESSION,))
        for step_id in (POPULATION_STEP, SEGMENTATION_STEP):
            cursor.execute(
                "INSERT INTO agent_runs (step_id, session_id, status) "
                "VALUES (%s, %s, 'pending') RETURNING agent_run_id",
                (step_id, SESSION),
            )
            ids[step_id] = str(cursor.fetchone()[0])
        for run in ids.values():
            cursor.execute(
                "UPDATE agent_runs SET status = 'running', started_at = now(), "
                "compiled_prompt = 'p', input_refs = '{\"schema_version\": 1}'::jsonb "
                "WHERE agent_run_id = %s",
                (run,),
            )
    connection.commit()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO cohorts (created_by_agent_run_id, cohort_key, criteria, label_definition) "
            "VALUES (%s, 'population', %s::jsonb, %s::jsonb) RETURNING cohort_id",
            (
                ids[POPULATION_STEP],
                json.dumps({"schema_version": 1}),
                json.dumps({"schema_version": 1, "kind": "binary", "field": "has_target_disease"}),
            ),
        )
        cohort = str(cursor.fetchone()[0])
        cursor.execute(
            "INSERT INTO segmentation_runs (created_by_agent_run_id, cohort_id, batch_key, config) "
            "VALUES (%s, %s, 'segmentation', %s::jsonb) RETURNING segmentation_run_id",
            (ids[SEGMENTATION_STEP], cohort,
             json.dumps({"schema_version": 1, "correlation_run_id": "corr-1"})),
        )
        ids["batch"] = str(cursor.fetchone()[0])
        cursor.execute(
            "INSERT INTO artifacts (session_id, kind, logical_key, display_filename, mime_type, "
            "byte_size, sha256, body, retention_class) VALUES (%s, 'export_csv', 'kept.csv', "
            "'kept.csv', 'text/csv', 3, %s, 'a,b'::bytea, 'user_retained') RETURNING artifact_id",
            (SESSION, "a" * 64),
        )
        ids["retained"] = str(cursor.fetchone()[0])
        cursor.execute(
            "INSERT INTO artifacts (session_id, kind, logical_key, display_filename, mime_type, "
            "byte_size, sha256, render_path, retention_class) VALUES (%s, 'figure_gif', "
            "'preview.gif', 'preview.gif', 'image/gif', 9, %s, 'overlays/p.gif', 'system_render') "
            "RETURNING artifact_id",
            (SESSION, "b" * 64),
        )
        ids["render"] = str(cursor.fetchone()[0])
        cursor.execute(
            "INSERT INTO ws_log (session_id, msg_type, payload) "
            "VALUES (%s, 'pipeline_agent_status', '{\"agents\": []}'::jsonb)",
            (SESSION,),
        )
        cursor.execute(
            "UPDATE agent_runs SET status = 'completed', completed_at = now() "
            "WHERE agent_run_id = %s",
            (ids[POPULATION_STEP],),
        )
    connection.commit()
    return ids


@pytest.fixture(scope="module")
def seeded():
    connection = _connect()
    with connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    connection.commit()
    _alembic("upgrade", PRE_CUTOVER)
    ids = _seed_n1(connection)
    _alembic("upgrade", CUTOVER_1)
    try:
        yield connection, ids
    finally:
        connection.close()


def test_the_eight_tables_are_renamed(seeded):
    connection, _ = seeded
    tables = _tables(connection)
    assert set(NEW_TABLES) <= tables
    assert not set(OLD_TABLES) & tables


def test_rows_keep_their_identity_under_the_new_columns(seeded):
    connection, ids = seeded
    assert _scalar(
        connection,
        "SELECT count(*) FROM node_runs r JOIN graph_nodes n ON n.node_id = r.node_id "
        "WHERE r.session_id = %s AND n.target_name IN "
        "('study_population_planner_agent', 'segmentation_agent')",
        (SESSION,),
    ) == 2
    assert _scalar(
        connection,
        "SELECT resolved_inputs->>'schema_version' FROM node_runs WHERE run_id = %s",
        (ids[SEGMENTATION_STEP],),
    ) == "1"
    assert _scalar(
        connection,
        "SELECT event_type FROM session_events WHERE session_id = %s",
        (SESSION,),
    ) == "pipeline_agent_status"  # historical payloads and types are not rewritten
    assert _scalar(
        connection, "SELECT control_state FROM sessions WHERE session_id = %s", (SESSION,)
    ) == "ready"


def test_the_batch_config_key_and_the_retention_values_are_rewritten(seeded):
    connection, ids = seeded
    config = _scalar(
        connection,
        "SELECT config FROM segmentation_batches WHERE segmentation_batch_id = %s",
        (ids["batch"],),
    )
    assert config == {"schema_version": 1, "batch_correlation_id": "corr-1"}
    assert _scalar(
        connection, "SELECT retention_class FROM stored_files WHERE file_id = %s", (ids["retained"],)
    ) == "retained"
    assert _scalar(
        connection, "SELECT retention_class FROM stored_files WHERE file_id = %s", (ids["render"],)
    ) == "render"
    # The re-created CHECK admits only the new values.
    assert "ck_stored_files_retention_class" in _rejects(
        connection,
        "INSERT INTO stored_files (session_id, kind, logical_key, display_filename, mime_type, "
        "byte_size, sha256, body, retention_class) VALUES (%s, 'export_csv', 'old.csv', "
        "'old.csv', 'text/csv', 3, %s, 'a,b'::bytea, 'user_retained')",
        (SESSION, "f" * 64),
    )


def test_the_recreated_triggers_still_enforce_their_rules(seeded):
    connection, ids = seeded
    assert "is completed and immutable" in _rejects(
        connection,
        "UPDATE node_runs SET scratchpad_notes = '{}'::jsonb WHERE run_id = %s",
        (ids[POPULATION_STEP],),
    )
    assert "render_path is immutable" in _rejects(
        connection,
        "UPDATE stored_files SET render_path = 'elsewhere.gif' WHERE file_id = %s",
        (ids["render"],),
    )
    assert "identity and config are immutable" in _rejects(
        connection,
        "UPDATE segmentation_batches SET batch_key = 'other' WHERE segmentation_batch_id = %s",
        (ids["batch"],),
    )
    assert "its nodes are immutable" in _rejects(
        connection, "UPDATE graph_nodes SET title = 'x' WHERE session_id = %s", (SESSION,)
    )
    assert "cannot be deleted" in _rejects(
        connection, "DELETE FROM session_graphs WHERE session_id = %s", (SESSION,)
    )
    # The producer rule now joins node_runs to graph_nodes.target_name.
    assert "rows are owned by" in _rejects(
        connection,
        "INSERT INTO feature_sets (created_by_run_id, cohort_id, set_key, feature_names, "
        "feature_matrix, selection, config_digest, source_digest, manifest_digest) "
        "SELECT %s, cohort_id, 'x', '[\"f\"]'::jsonb, '{}'::jsonb, "
        "'{\"schema_version\": 1, \"label_supervised_on_full_data\": false}'::jsonb, "
        "%s, %s, %s FROM cohorts LIMIT 1",
        (ids[SEGMENTATION_STEP], "c" * 64, "d" * 64, "e" * 64),
    )


def test_no_function_body_still_names_a_renamed_table_or_column(seeded):
    connection, _ = seeded
    assert _scalar(
        connection,
        "SELECT count(*) FROM pg_proc WHERE pronamespace = 'public'::regnamespace "
        "AND prosrc ~ '(agent_run|plan_steps|artifact_id|segmentation_run|external_job_id|"
        "input_refs|user_retained|system_render|FROM plans|FROM artifacts)'",
    ) == 0


def test_downgrade_restores_the_n1_names_and_values(seeded):
    connection, ids = seeded
    _alembic("downgrade", PRE_CUTOVER)
    try:
        tables = _tables(connection)
        assert set(OLD_TABLES) <= tables
        assert not set(NEW_TABLES) & tables
        assert _scalar(
            connection,
            "SELECT retention_class FROM artifacts WHERE artifact_id = %s",
            (ids["retained"],),
        ) == "user_retained"
        assert _scalar(
            connection,
            "SELECT config->>'correlation_run_id' FROM segmentation_runs "
            "WHERE segmentation_run_id = %s",
            (ids["batch"],),
        ) == "corr-1"
        assert "is completed and immutable" in _rejects(
            connection,
            "UPDATE agent_runs SET durable_notes = '{}'::jsonb WHERE agent_run_id = %s",
            (ids[POPULATION_STEP],),
        )
    finally:
        _alembic("upgrade", CUTOVER_1)
