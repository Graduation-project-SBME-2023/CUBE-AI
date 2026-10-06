"""Static contract of the session-graph migration (p1f0a2c3d4e5_v1_p_session_graph): what the
deterministic gate can check without a database. The live behaviour is in
tests/integration/test_v1_p_session_graph_live.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "application_api" / "migrations" / "versions" / "p1f0a2c3d4e5_v1_p_session_graph.py"
)


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location("_cubeai_v1_p_session_graph", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_table_a_run_produces_is_fenced(migration):
    """A new table with created_by_run_id without the trigger fails here."""
    import application_api.db.models  # noqa: F401  (registers every mapper)
    from application_api.db.base import Base

    produced = {
        name for name, table in Base.metadata.tables.items() if "created_by_run_id" in table.columns
    }
    assert produced <= set(migration.FENCED_TABLES)


def test_the_execution_and_checkpoint_tables_are_fenced(migration):
    assert {"node_runs", "run_attempts", "stored_files", *migration.CHECKPOINT_TABLES} <= set(
        migration.FENCED_TABLES
    )


def test_every_fenced_table_gets_exactly_one_fencing_trigger(migration):
    fencing = [table for name, table, _ in migration.P_TRIGGERS if name.endswith("_fencing")]
    assert sorted(fencing) == sorted(migration.FENCED_TABLES)


def test_the_extended_allow_lists_keep_every_legacy_producer(migration):
    for table, legacy in migration.V1_PRODUCERS.items():
        assert set(legacy) <= set(migration.PRODUCERS[table])


def test_every_fenced_result_table_names_its_producers(migration):
    """A result table without an allow-list would accept rows from any node."""
    result_tables = set(migration.FENCED_TABLES) - {
        "node_runs", "run_attempts", *migration.CHECKPOINT_TABLES
    }
    assert set(migration.PRODUCERS) == result_tables - set(migration.UNRESTRICTED_PRODUCERS)
    assert all(migration.PRODUCERS.values()), "an empty list admits any target"


def test_each_legacy_agent_may_still_produce_its_port_table(migration):
    for port in migration.LEGACY_PORTS:
        if port.table in migration.PRODUCERS:
            assert port.target_name in migration.PRODUCERS[port.table]


def test_legacy_outputs_map_only_canonical_agents(migration):
    from application_api.core.agent_types import CANONICAL_AGENT_TYPES

    targets = [port.target_name for port in migration.LEGACY_PORTS]
    assert len(targets) == len(set(targets)), "one port per legacy agent"
    assert set(targets) <= set(CANONICAL_AGENT_TYPES)


def test_the_attempt_state_sets_partition_the_lifecycle(migration):
    active, terminal = set(migration.ACTIVE_ATTEMPT_STATES), set(migration.TERMINAL_ATTEMPT_STATES)
    assert not active & terminal
    assert "suspended" in active and "unknown" in active


def test_downgrade_refuses_every_case_of_step_12(migration):
    reasons = " | ".join(reason for reason, _ in migration.DOWNGRADE_REFUSALS)
    for case in (
        "more than one run",
        "more than one attempt",
        "draft was moved into a change",
        "user other than the legacy user",
        "non-legacy graph change",
        "agent checkpoint",
        "attempt is suspended",
        # Rows of new tables or vocabulary that the o1 schema cannot hold.
        "tool message",
        "training job",
    ):
        assert case in reasons
