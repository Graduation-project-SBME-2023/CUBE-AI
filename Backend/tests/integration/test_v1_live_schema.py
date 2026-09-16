"""Live migration and constraint checks."""

from __future__ import annotations

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

pytestmark.append(
    pytest.mark.skipif(
        not LIVE_DSN,
        reason="set V1_LIVE_DATABASE_URL to a disposable PostgreSQL 18 database",
    )
)


def _alembic(command: str, revision: str) -> str:
    """Run alembic against the disposable database and return its output."""
    environment = dict(os.environ)
    # env.py rewrites the async driver to psycopg2 itself.
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
    return completed.stdout + completed.stderr


def _connect():
    connection = psycopg2.connect(LIVE_DSN)
    connection.autocommit = False
    return connection


def _reset_schema() -> None:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        connection.commit()
    finally:
        connection.close()


SESSION_A = uuid.uuid4()  # approved legacy plan with steps and task messages
SESSION_B = uuid.uuid4()  # draft plan
SESSION_C = uuid.uuid4()  # legacy "confirmed" plan with NO steps


def _seed_pre_f(connection) -> None:
    """A synthetic pre-step-F database: what step F has to migrate."""
    with connection.cursor() as cursor:
        for session_id, prompt, approved in (
            (SESSION_A, "does bin width change AUC?", True),
            (SESSION_B, "which spleens look abnormal?", False),
            (SESSION_C, "an approval that never had steps", True),
        ):
            cursor.execute(
                "INSERT INTO sessions (session_id, title, prompt, prompt_kind, status) "
                "VALUES (%s, %s, %s, 'general', 'planning')",
                (str(session_id), "fixture", prompt),
            )
            # Steps are inserted while the plan is still a draft — the step
            # immutability trigger from step E is real, and the fixture has to
            # respect it exactly as the application does.
            cursor.execute(
                "INSERT INTO plans (session_id, approved_at, updated_at) "
                "VALUES (%s, NULL, now())",
                (str(session_id),),
            )

        # Two steps naming the SAME agent type — 's case.
        step_ids = [uuid.uuid4(), uuid.uuid4()]
        for order, step_id in enumerate(step_ids, start=1):
            cursor.execute(
                "INSERT INTO plan_steps (step_id, session_id, step_order, agent_type, "
                "title, instructions) VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
                (
                    str(step_id),
                    str(SESSION_A),
                    order,
                    "segmentation_agent",
                    f"Segment pass {order}",
                    '{"description": "segment the spleen"}',
                ),
            )
            augmentation_id = uuid.uuid4()
            cursor.execute(
                "INSERT INTO context_augmentations (id, knowledge_summary) VALUES (%s, %s)",
                (str(augmentation_id), f"spleen knowledge {order}"),
            )
            cursor.execute(
                "INSERT INTO task_messages (id, agent_id, task_id, phase, agent_title, "
                "instructions, hypothesis_summary, payload, context_augmentation_id) "
                "VALUES (%s, %s, %s, 'segmentation', %s, %s, %s, %s::jsonb, %s)",
                (
                    str(uuid.uuid4()),
                    str(step_id),
                    f"task-{order}",
                    f"Segment pass {order}",
                    f"compiled instructions {order}",
                    "spleen hypothesis",
                    '{"bin_width": 25}',
                    str(augmentation_id),
                ),
            )

        cursor.execute(
            "INSERT INTO plan_steps (step_id, session_id, step_order, agent_type, title, "
            "instructions) VALUES (%s, %s, 1, 'modeling_agent', 'Model', '{}'::jsonb)",
            (str(uuid.uuid4()), str(SESSION_B)),
        )

        # Now approve the two legacy plans, exactly as the legacy `state`
        # backfill in step E left them: approved, with no runs anywhere.
        cursor.execute(
            "UPDATE plans SET approved_at = '2026-09-01T00:00:00Z' WHERE session_id IN (%s, %s)",
            (str(SESSION_A), str(SESSION_C)),
        )

        # ws_log rows inserted out of chronological order on purpose: step F
        # must renumber by created_at, not by physical row order.
        for offset, msg_type in ((3, "third"), (1, "first"), (2, "second")):
            cursor.execute(
                "INSERT INTO ws_log (uuid, session_id, msg_type, payload, created_at) "
                "VALUES (%s, %s, %s, %s::jsonb, %s)",
                (
                    str(uuid.uuid4()),
                    str(SESSION_A),
                    msg_type,
                    '{"n": 1}',
                    f"2026-09-01T00:0{offset}:00Z",
                ),
            )
        cursor.execute(
            "INSERT INTO ws_log (uuid, session_id, msg_type, payload, created_at) "
            "VALUES (%s, %s, NULL, NULL, %s)",
            (str(uuid.uuid4()), str(SESSION_A), "2026-09-01T00:04:00Z"),
        )
    connection.commit()


@pytest.fixture(scope="module")
def migrated():
    """Fresh database → pre-F fixture → head. Yields a connection."""
    _reset_schema()
    _alembic("upgrade", "e1f0a2c3d4e5")
    connection = _connect()
    _seed_pre_f(connection)
    connection.close()

    output = _alembic("upgrade", "head")

    connection = _connect()
    try:
        yield connection, output
    finally:
        connection.close()


def _scalar(connection, sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    connection.rollback()
    return row[0] if row else None


def _rows(connection, sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    connection.rollback()
    return rows


# ----------------------------------------------------------------------
# Step F — the execution spine
# ----------------------------------------------------------------------
def test_task_messages_are_folded_into_the_step_definition(migrated):
    connection, output = migrated
    assert "folded 2 task message(s)" in output
    assert _scalar(
        connection,
        "SELECT count(*) FROM information_schema.tables WHERE table_name IN "
        "('task_messages', 'context_augmentations')",
    ) == 0
    folded = _rows(
        connection,
        "SELECT instructions->'task_message'->>'task_id', "
        "       instructions->'task_message'->>'instructions', "
        "       instructions->'task_message'->'context_augmentation'->>'knowledge_summary', "
        "       instructions->>'description' "
        "FROM plan_steps WHERE session_id = %s ORDER BY step_order",
        (str(SESSION_A),),
    )
    assert [row[0] for row in folded] == ["task-1", "task-2"]
    assert folded[0][1] == "compiled instructions 1"
    assert folded[0][2] == "spleen knowledge 1"
    # 's earlier fold is untouched.
    assert folded[0][3] == "segment the spleen"


def test_approval_gets_one_pending_run_per_step_and_a_draft_gets_none(migrated):
    connection, _ = migrated
    runs = _rows(
        connection,
        "SELECT r.status, r.started_at, r.compiled_prompt, s.step_order "
        "FROM agent_runs r JOIN plan_steps s ON s.step_id = r.step_id "
        "WHERE r.session_id = %s ORDER BY s.step_order",
        (str(SESSION_A),),
    )
    assert [row[0] for row in runs] == ["pending", "pending"]
    # Nothing claims to have executed: no start time and no compiled prompt.
    assert all(row[1] is None and row[2] is None for row in runs)

    assert _scalar(
        connection, "SELECT count(*) FROM agent_runs WHERE session_id = %s", (str(SESSION_B),)
    ) == 0


def test_an_approved_plan_with_no_steps_is_returned_to_draft(migrated):
    connection, output = migrated
    assert "returned to draft" in output
    assert _scalar(
        connection, "SELECT approved_at FROM plans WHERE session_id = %s", (str(SESSION_C),)
    ) is None


def test_ws_log_is_renumbered_in_created_at_order(migrated):
    connection, _ = migrated
    events = _rows(
        connection,
        "SELECT msg_type FROM ws_log WHERE session_id = %s ORDER BY event_id",
        (str(SESSION_A),),
    )
    assert [row[0] for row in events] == ["first", "second", "third", "legacy_untyped"]
    # Identity, not UUID, and the cursor index exists.
    assert _scalar(
        connection,
        "SELECT is_identity FROM information_schema.columns WHERE table_name = 'ws_log' "
        "AND column_name = 'event_id'",
    ) == "YES"
    assert _scalar(
        connection,
        "SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_ws_log_session_id_event_id'",
    ) == 1
    assert _scalar(
        connection,
        "SELECT count(*) FROM information_schema.columns WHERE table_name = 'ws_log' "
        "AND column_name = 'uuid'",
    ) == 0


def test_ws_log_identity_caches_one_value(migrated):
    """depends on ids being handed out one at a time, not in blocks."""
    connection, _ = migrated
    cache = _scalar(
        connection,
        "SELECT cache_size FROM pg_sequences WHERE sequencename LIKE %s",
        ("ws_log%",),
    )
    assert cache == 1


def test_a_draft_plan_cannot_hold_a_run(migrated):
    connection, _ = migrated
    step_id = _scalar(
        connection,
        "SELECT step_id FROM plan_steps WHERE session_id = %s",
        (str(SESSION_B),),
    )
    with pytest.raises(psycopg2.Error) as excinfo:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO agent_runs (step_id, session_id, status) VALUES (%s, %s, 'pending')",
                (str(step_id), str(SESSION_B)),
            )
        connection.commit()
    connection.rollback()
    assert "draft plan" in str(excinfo.value)


def test_a_second_run_for_one_step_is_rejected(migrated):
    connection, _ = migrated
    step_id = _scalar(
        connection,
        "SELECT step_id FROM plan_steps WHERE session_id = %s ORDER BY step_order LIMIT 1",
        (str(SESSION_A),),
    )
    with pytest.raises(psycopg2.Error) as excinfo:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO agent_runs (step_id, session_id, status) VALUES (%s, %s, 'pending')",
                (str(step_id), str(SESSION_A)),
            )
        connection.commit()
    connection.rollback()
    assert "uq_agent_runs_step_id" in str(excinfo.value)


def test_an_approved_step_cannot_be_edited_or_added(migrated):
    connection, _ = migrated
    with pytest.raises(psycopg2.Error):
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE plan_steps SET title = 'edited' WHERE session_id = %s",
                (str(SESSION_A),),
            )
        connection.commit()
    connection.rollback()


def test_run_transitions_and_immutability(migrated):
    connection, _ = migrated
    run_id = _scalar(
        connection,
        "SELECT r.agent_run_id FROM agent_runs r JOIN plan_steps s ON s.step_id = r.step_id "
        "WHERE r.session_id = %s ORDER BY s.step_order LIMIT 1",
        (str(SESSION_A),),
    )

    # A run cannot start without its compiled prompt and inputs.
    with pytest.raises(psycopg2.Error):
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE agent_runs SET status = 'running', started_at = now() "
                "WHERE agent_run_id = %s",
                (str(run_id),),
            )
        connection.commit()
    connection.rollback()

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE agent_runs SET status = 'running', started_at = now(), "
            "compiled_prompt = 'compiled', input_refs = '{\"schema_version\": 1}'::jsonb "
            "WHERE agent_run_id = %s",
            (str(run_id),),
        )
    connection.commit()

    # compiled_prompt is immutable once the run has started.
    with pytest.raises(psycopg2.Error) as excinfo:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE agent_runs SET compiled_prompt = 'rewritten' WHERE agent_run_id = %s",
                (str(run_id),),
            )
        connection.commit()
    connection.rollback()
    assert "immutable once" in str(excinfo.value)

    # pending is not reachable again.
    with pytest.raises(psycopg2.Error):
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE agent_runs SET status = 'pending', started_at = NULL "
                "WHERE agent_run_id = %s",
                (str(run_id),),
            )
        connection.commit()
    connection.rollback()

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE agent_runs SET status = 'completed', completed_at = now() "
            "WHERE agent_run_id = %s",
            (str(run_id),),
        )
    connection.commit()

    # A completed run is frozen, and no run is ever deleted.
    with pytest.raises(psycopg2.Error) as excinfo:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE agent_runs SET output_summary = '{}'::jsonb WHERE agent_run_id = %s",
                (str(run_id),),
            )
        connection.commit()
    connection.rollback()
    assert "completed and immutable" in str(excinfo.value)

    with pytest.raises(psycopg2.Error) as excinfo:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM agent_runs WHERE agent_run_id = %s", (str(run_id),))
        connection.commit()
    connection.rollback()
    assert "cannot be deleted" in str(excinfo.value)


# ----------------------------------------------------------------------
# Step G — artifacts
# ----------------------------------------------------------------------
def _insert_artifact(connection, **columns):
    columns.setdefault("session_id", str(SESSION_A))
    columns.setdefault("kind", "figure_png")
    columns.setdefault("display_filename", "figure.png")
    columns.setdefault("mime_type", "image/png")
    columns.setdefault("retention_class", "user_retained")
    names = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO artifacts ({names}) VALUES ({placeholders}) RETURNING artifact_id",
            tuple(columns.values()),
        )
        artifact_id = cursor.fetchone()[0]
    connection.commit()
    return artifact_id


def test_retained_bytes_and_renders_are_mutually_exclusive(migrated):
    """as a CHECK: durable bytes or a regenerable render, never both."""
    connection, _ = migrated
    digest = "a" * 64

    # user_retained with no body is rejected...
    with pytest.raises(psycopg2.Error) as excinfo:
        _insert_artifact(
            connection,
            logical_key="retained-without-bytes",
            byte_size=10,
            sha256=digest,
        )
    connection.rollback()
    assert "retention_shape" in str(excinfo.value)

    # ...and so is a system_render that carries bytes.
    with pytest.raises(psycopg2.Error) as excinfo:
        _insert_artifact(
            connection,
            logical_key="render-with-bytes",
            byte_size=4,
            sha256=digest,
            body=psycopg2.Binary(b"data"),
            render_path="overlays/x.gif",
            retention_class="system_render",
            kind="figure_gif",
            mime_type="image/gif",
        )
    connection.rollback()
    assert "retention_shape" in str(excinfo.value)


def test_zero_bytes_and_a_fake_digest_are_rejected(migrated):
    connection, _ = migrated
    with pytest.raises(psycopg2.Error):
        _insert_artifact(
            connection,
            logical_key="empty",
            byte_size=0,
            sha256="b" * 64,
            body=psycopg2.Binary(b""),
        )
    connection.rollback()

    with pytest.raises(psycopg2.Error) as excinfo:
        _insert_artifact(
            connection,
            logical_key="not-a-digest",
            byte_size=4,
            sha256="nope",
            body=psycopg2.Binary(b"data"),
        )
    connection.rollback()
    assert "sha256" in str(excinfo.value)


def test_a_render_path_must_be_a_relative_key(migrated):
    """Never a host absolute path, never a signed URL, never a traversal."""
    connection, _ = migrated
    for bad in ("/var/lib/overlays/x.gif", "C:\\overlays\\x.gif", "../x.gif", "https://x/y.gif"):
        with pytest.raises(psycopg2.Error) as excinfo:
            _insert_artifact(
                connection,
                logical_key=f"render-{bad}",
                byte_size=4,
                sha256="c" * 64,
                render_path=bad,
                retention_class="system_render",
                kind="figure_gif",
                mime_type="image/gif",
            )
        connection.rollback()
        assert "render_path_is_relative" in str(excinfo.value)


def test_one_logical_key_per_session(migrated):
    connection, _ = migrated
    _insert_artifact(
        connection,
        logical_key="figures/roc",
        byte_size=4,
        sha256="d" * 64,
        body=psycopg2.Binary(b"data"),
    )
    with pytest.raises(psycopg2.Error) as excinfo:
        _insert_artifact(
            connection,
            logical_key="figures/roc",
            byte_size=5,
            sha256="e" * 64,
            body=psycopg2.Binary(b"data2"),
        )
    connection.rollback()
    assert "uq_artifacts_session_id" in str(excinfo.value)


def test_retained_bytes_are_immutable_but_a_render_digest_refreshes(migrated):
    connection, _ = migrated
    retained = _insert_artifact(
        connection,
        logical_key="figures/immutable",
        byte_size=4,
        sha256="f" * 64,
        body=psycopg2.Binary(b"data"),
    )
    with pytest.raises(psycopg2.Error) as excinfo:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE artifacts SET body = %s, sha256 = %s, byte_size = 5 "
                "WHERE artifact_id = %s",
                (psycopg2.Binary(b"other"), "0" * 64, retained),
            )
        connection.commit()
    connection.rollback()
    assert "immutable" in str(excinfo.value)

    render = _insert_artifact(
        connection,
        logical_key="overlay/pair-1",
        byte_size=4,
        sha256="1" * 64,
        render_path="overlays/pair-1.gif",
        retention_class="system_render",
        kind="figure_gif",
        mime_type="image/gif",
    )
    # The render is a cache entry — regenerating it legitimately changes
    # the bytes under the same key, so the catalogue records the new digest.
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE artifacts SET sha256 = %s, byte_size = 9 WHERE artifact_id = %s",
            ("2" * 64, render),
        )
    connection.commit()

    # ...but it cannot be re-pointed at a different file.
    with pytest.raises(psycopg2.Error) as excinfo:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE artifacts SET render_path = %s WHERE artifact_id = %s",
                ("overlays/other.gif", render),
            )
        connection.commit()
    connection.rollback()
    assert "render_path is immutable" in str(excinfo.value)


def test_an_artifact_cannot_borrow_another_sessions_run(migrated):
    """When the run link is present it must be a run of THIS session."""
    connection, _ = migrated
    other_run = _scalar(
        connection,
        "SELECT agent_run_id FROM agent_runs WHERE session_id = %s LIMIT 1",
        (str(SESSION_A),),
    )
    with pytest.raises(psycopg2.Error) as excinfo:
        _insert_artifact(
            connection,
            session_id=str(SESSION_B),
            logical_key="cross-session",
            byte_size=4,
            sha256="3" * 64,
            body=psycopg2.Binary(b"data"),
            created_by_agent_run_id=str(other_run),
        )
    connection.rollback()
    assert "agent_run" in str(excinfo.value)


def test_every_v1_revision_is_reversible_on_a_populated_database(migrated):
    connection, _ = migrated
    connection.close()
    before = set(
        row[0]
        for row in _rows(
            _connect(),
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'",
        )
    )
    _alembic("downgrade", "c9d0e1f2a3b4")
    _alembic("upgrade", "head")
    after = set(
        row[0]
        for row in _rows(
            _connect(),
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'",
        )
    )
    assert before == after
