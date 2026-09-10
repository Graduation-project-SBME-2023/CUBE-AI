from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
APPLICATION_ROOT = BACKEND_ROOT / "application_api"

# Migrations live under Backend/application_api/migrations/, not
# Backend/migrations/ — the latter was asserted NOT to exist by
# test_service_route_ownership.py:test_database_source_tree_is_owned_only_by_application_api,
# which this test previously contradicted (it read a path the other test
# asserted was absent). See cubeai-docs/backend/database_audit.md D-34.


def test_index_migration_uses_the_volume_model_table_name():
    migration = (
        APPLICATION_ROOT
        / "migrations"
        / "versions"
        / "f1a2b3c4d5e6_add_application_api_indexes.py"
    ).read_text(encoding="utf-8")

    assert '"volume_series"' in migration
    assert '"volumes"' not in migration


def test_alembic_env_registers_relationship_models_for_autogenerate():
    """
    There is no standalone monailabel/models/__init__.py seeder registry
    anymore (Backend/monailabel/models/ cannot exist post-refactor — see
    test_service_route_ownership.py). The equivalent contract — that
    Alembic's autogenerate can see every relationship-bearing model, not
    just the ones a given migration happens to touch — is enforced by
    migrations/env.py importing them directly against Base.metadata.
    """
    env_source = (APPLICATION_ROOT / "migrations" / "env.py").read_text(encoding="utf-8")

    assert "from application_api.db.models.hypothesis import Hypothesis" in env_source
    assert "from application_api.db.models.plan import Plan" in env_source
    assert "from application_api.db.models.steps import PlanStep" in env_source
    assert "from application_api.db.models.Taskmessage import TaskMessage" in env_source
