"""Live checks of the agent-transcript migration (t1f0a2c3d4e5_v1_t_agent_transcripts).

Against a disposable PostgreSQL 18 database: the migration applies and
reverses, the checkpoint tables and the Main's tool-call contract go and come
back, and downgrade() refuses once this migration's data exists.

Run (from Backend/, with a throwaway database):

    V1_LIVE_DATABASE_URL=postgresql://user:pw@host:5432/db \
    python -m pytest -c pytest.ini tests/integration/test_v1_t_agent_transcripts_live.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

psycopg2 = pytest.importorskip("psycopg2", reason="psycopg2 is required for live checks")

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "application_api" / "alembic.ini"
LIVE_DSN = os.environ.get("V1_LIVE_DATABASE_URL")
BEFORE = "r1f0a2c3d4e5"
CUTOVER_3 = "t1f0a2c3d4e5"

pytestmark.append(
    pytest.mark.skipif(not LIVE_DSN, reason="set V1_LIVE_DATABASE_URL to a disposable PostgreSQL 18 database")
)


def _alembic(command: str, revision: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), command, revision],
        cwd=str(BACKEND_ROOT), env=dict(os.environ, DATABASE_URL=LIVE_DSN), capture_output=True, text=True,
    )


def _migrate(command: str, revision: str) -> None:
    completed = _alembic(command, revision)
    assert completed.returncode == 0, f"alembic {command} {revision} failed:\n{completed.stderr}"


def _scalar(sql: str, params: tuple = ()):
    connection = psycopg2.connect(LIVE_DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
        connection.rollback()
        return row[0] if row else None
    finally:
        connection.close()


def _execute(sql: str, params: tuple = ()) -> None:
    connection = psycopg2.connect(LIVE_DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
        connection.commit()
    finally:
        connection.close()


def _tables() -> set[str]:
    connection = psycopg2.connect(LIVE_DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            return {row[0] for row in cursor.fetchall()}
    finally:
        connection.close()


def _trigger_exists(name: str) -> bool:
    return bool(_scalar("SELECT count(*) FROM pg_trigger WHERE tgname = %s AND NOT tgisinternal", (name,)))


@pytest.fixture
def at_r1():
    _execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    _migrate("upgrade", BEFORE)
    yield


def test_the_migration_applies_and_reverses(at_r1):
    _migrate("upgrade", CUTOVER_3)
    tables = _tables()
    assert {"agent_transcript_entries", "main_transcript_entries"} <= tables
    assert not {"agent_checkpoints", "agent_checkpoint_writes"} & tables
    assert not _trigger_exists("chat_messages_tool_pairing")
    assert _trigger_exists("agent_transcript_entries_fencing") and _trigger_exists("graph_nodes_user_release")
    assert _scalar("SELECT column_default FROM information_schema.columns "
                   "WHERE table_name = 'graph_nodes' AND column_name = 'approval'") == "'none'::text"

    _migrate("downgrade", BEFORE)
    tables = _tables()
    assert {"agent_checkpoints", "agent_checkpoint_writes"} <= tables
    assert not {"agent_transcript_entries", "main_transcript_entries"} & tables
    assert _trigger_exists("chat_messages_tool_pairing") and _trigger_exists("agent_checkpoints_fencing")
    assert not _scalar("SELECT count(*) FROM information_schema.columns "
                       "WHERE table_name = 'run_attempts' AND column_name = 'pending_tools'")

    _migrate("upgrade", CUTOVER_3)  # and forward again
    assert {"agent_transcript_entries"} <= _tables()


def _session_with_a_main_entry() -> str:
    """A session (owned by the migration's `legacy` user, with its graph, as T0
    creates them) whose Main session holds one entry."""
    _execute(
        "WITH s AS (INSERT INTO sessions (user_id, prompt) SELECT user_id, 'synthetic question' FROM users "
        "LIMIT 1 RETURNING session_id) INSERT INTO session_graphs (session_id) SELECT session_id FROM s"
    )
    session_id = _scalar("SELECT session_id FROM sessions LIMIT 1")
    _execute(
        "INSERT INTO main_transcript_entries (session_id, entry_uuid, entry) VALUES (%s, 'u1', '{\"uuid\": \"u1\"}')",
        (session_id,),
    )
    return session_id


def test_downgrade_refuses_once_a_main_transcript_exists(at_r1):
    _migrate("upgrade", CUTOVER_3)
    _session_with_a_main_entry()
    completed = _alembic("downgrade", BEFORE)
    assert completed.returncode != 0
    assert "Refusing to downgrade the agent-transcript migration" in completed.stderr
    assert "a Main transcript entry exists" in completed.stderr
    assert _scalar("SELECT version_num FROM alembic_version") == CUTOVER_3


def test_transcript_entries_are_immutable(at_r1):
    _migrate("upgrade", CUTOVER_3)
    _session_with_a_main_entry()
    with pytest.raises(psycopg2.Error, match="immutable"):
        _execute("UPDATE main_transcript_entries SET entry = '{\"uuid\": \"u1\", \"x\": 1}'")
