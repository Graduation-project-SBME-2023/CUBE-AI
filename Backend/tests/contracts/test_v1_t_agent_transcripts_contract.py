"""Static contract of the agent-transcript migration (t1f0a2c3d4e5_v1_t_agent_transcripts):
what the deterministic gate can check without a database. The live behaviour is in
tests/integration/test_v1_t_agent_transcripts_live.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

VERSIONS = Path(__file__).resolve().parents[2] / "application_api" / "migrations" / "versions"


def _load(name: str):
    path = VERSIONS / name
    spec = importlib.util.spec_from_file_location(f"_cubeai_{path.stem}_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration():
    return _load("t1f0a2c3d4e5_v1_t_agent_transcripts.py")


@pytest.fixture(scope="module")
def session_graph():
    return _load("p1f0a2c3d4e5_v1_p_session_graph.py")


def test_it_follows_the_head_it_was_written_on(migration):
    assert (migration.revision, migration.down_revision) == ("t1f0a2c3d4e5", "r1f0a2c3d4e5")


def test_the_transcript_table_replaces_the_checkpoint_tables_under_the_fence(migration, session_graph):
    expected = (set(session_graph.FENCED_TABLES) - set(session_graph.CHECKPOINT_TABLES)) | {"agent_transcript_entries"}
    assert set(migration.FENCED_TABLES) == expected
    assert "main_transcript_entries" not in migration.FENCED_TABLES  # the Main's session is not an execution table
    fencing = [table for name, table, _ in migration.T_TRIGGERS if name.endswith("_fencing")]
    assert fencing == ["agent_transcript_entries"]


def test_every_table_a_run_produces_is_fenced(migration):
    import application_api.db.models  # noqa: F401  (registers every mapper)
    from application_api.db.base import Base

    tables = Base.metadata.tables
    produced = {name for name, table in tables.items() if "created_by_run_id" in table.columns}
    assert produced <= set(migration.FENCED_TABLES)
    assert {"agent_transcript_entries", "main_transcript_entries"} <= set(tables)
    assert not {"agent_checkpoints", "agent_checkpoint_writes"} & set(tables)


def test_the_suspension_kinds_match_the_model(migration):
    from application_api.db.models.run_attempts import QUESTION_KINDS, SUSPENSION_KINDS

    assert (migration.QUESTIONS, migration.SUSPENSION_KINDS) == (QUESTION_KINDS, SUSPENSION_KINDS)


def test_upgrade_and_downgrade_name_the_same_rules(migration):
    created = {name for name, _, _ in migration.T_TRIGGERS}
    assert created == {
        "agent_transcript_entries_fencing", "agent_transcript_entries_immutable",
        "main_transcript_entries_immutable", "graph_nodes_user_release", "node_runs_user_approvals",
    }
    for function in migration.T_FUNCTIONS:
        assert function.split("(")[0] in migration._FUNCTIONS_T


def test_the_fencing_function_knows_the_transcript_table_and_no_checkpoint(migration):
    body = migration._DOMAIN_ROWS_FENCING_T
    assert "agent_transcript_entries" in body and "agent_checkpoint" not in body
    assert "suspension ->> 'kind' = 'ask_user'" in body  # the user path answers an ask_user form


def test_downgrade_refuses_every_value_this_migration_brings(migration):
    reasons = " | ".join(reason for reason, _ in migration.DOWNGRADE_REFUSALS)
    for case in (
        "agent transcript entry", "Main transcript entry", "approval", "user review",
        "frozen automatically", "review was accepted automatically", "pair was accepted automatically",
        "pending tools", "v2 suspension",
    ):
        assert case in reasons
