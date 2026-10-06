"""The schema-drift check behind the Application API readiness probe, without a database."""

from __future__ import annotations

import pytest

from application_api.core.schema_version import code_heads, schema_drift

pytestmark = pytest.mark.unit


def test_code_heads_reads_the_single_migration_head():
    heads = code_heads()
    assert len(heads) == 1


def test_matching_revision_is_not_drift():
    assert schema_drift(["abc123"], frozenset({"abc123"})) is None


def test_older_revision_is_drift_and_says_how_to_fix_it():
    reason = schema_drift(["old111"], frozenset({"new222"}))
    assert reason is not None
    assert "old111" in reason and "new222" in reason
    assert "upgrade head" in reason


def test_empty_alembic_version_is_drift():
    reason = schema_drift([], frozenset({"new222"}))
    assert reason is not None
    assert "no revision" in reason
