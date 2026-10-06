from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

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


def test_legacy_gender_constraint_name_bypasses_naming_convention():
    migration = (
        APPLICATION_ROOT
        / "migrations"
        / "versions"
        / "db570196426f_remove_gender_check_constraint.py"
    ).read_text(encoding="utf-8")

    # The initial SQL migration created patients_gender_check verbatim. Without
    # op.f(), Alembic's current naming convention expands it to the nonexistent
    # ck_patients_patients_gender_check during upgrade or downgrade.
    assert migration.count('op.f("patients_gender_check")') == 2


def test_uuid_conversion_drops_naming_convention_foreign_keys():
    migration = (
        APPLICATION_ROOT
        / "migrations"
        / "versions"
        / "6664d514406c_create_hypothesis_plan_plan_step_tables.py"
    ).read_text(encoding="utf-8")

    assert "fk_chat_messages_chat_id_chat_sessions" in migration
    assert "fk_chat_study_association_chat_id_chat_sessions" in migration
    assert "pk_chat_messages" in migration


def test_alembic_env_registers_every_model_for_autogenerate():
    """Autogenerate only sees tables registered on Base.metadata. env.py imports
    the models package, and the package imports every model module, so a model
    file left out of db/models/__init__.py would make autogenerate propose
    dropping its table."""
    env_source = (APPLICATION_ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
    assert "import application_api.db.models" in env_source
    assert "target_metadata = Base.metadata" in env_source

    models_dir = APPLICATION_ROOT / "db" / "models"
    package_source = (models_dir / "__init__.py").read_text(encoding="utf-8")
    modules = sorted(path.stem for path in models_dir.glob("*.py") if path.stem != "__init__")
    unregistered = [name for name in modules if f"from .{name} import" not in package_source]
    assert modules
    assert not unregistered, f"model modules missing from db/models/__init__.py: {unregistered}"
