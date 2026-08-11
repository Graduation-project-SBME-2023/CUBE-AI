from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_index_migration_uses_the_volume_model_table_name():
    migration = (
        BACKEND_ROOT
        / "migrations"
        / "versions"
        / "f1a2b3c4d5e6_add_application_api_indexes.py"
    ).read_text(encoding="utf-8")

    assert '"volume_series"' in migration
    assert '"volumes"' not in migration


def test_standalone_seeder_registers_relationship_models():
    model_registry = (
        BACKEND_ROOT / "monailabel" / "models" / "__init__.py"
    ).read_text(encoding="utf-8")

    assert "from .hypothesis import Hypothesis" in model_registry
    assert "from .plan import Plan" in model_registry
    assert "from .steps import PlanStep" in model_registry
    assert "from .Taskmessage import TaskMessage" in model_registry
