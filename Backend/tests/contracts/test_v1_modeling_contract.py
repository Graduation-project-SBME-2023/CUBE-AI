"""Contract tests for this migration — modeling."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
APPLICATION_ROOT = BACKEND_ROOT / "application_api"
TOOLPACKS_ROOT = BACKEND_ROOT / "agent_system" / "toolpacks"
AGENT_ROOT = BACKEND_ROOT / "agent_system"
VERSIONS = APPLICATION_ROOT / "migrations" / "versions"
MODELING_VIEW = (
    REPO_ROOT
    / "Frontend/OHIF-Viewer/extensions/research-playground/src/components/PlaygroundCanvas/views/ModelingView.tsx"
)


def _table(name: str):
    import application_api.db.models  # noqa: F401
    from application_api.db.base import Base

    return Base.metadata.tables[name]


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _migration_path(revision: str) -> Path:
    for path in VERSIONS.glob("*.py"):
        if f'revision: str = "{revision}"' in path.read_text(encoding="utf-8"):
            return path
    raise AssertionError(f"no migration declares revision {revision}")


# ----------------------------------------------------------------------
# model_results
# ----------------------------------------------------------------------
def test_model_results_have_the_v1_shape():
    columns = set(_table("model_results").columns.keys())
    assert columns == {
        "model_result_id",
        "created_by_run_id",
        "cohort_id",
        "feature_set_id",
        "comparison_key",
        "variant_key",
        "algorithm",
        "parameters",
        "preprocessing",
        "feature_order",
        "split_definition",
        "seeds",
        "environment",
        "metrics",
        "dataset_digest",
        "predictions_file_id",
        "input_file_id",
        "selected",
        "model_file_name",
        "model_file_digest",
        "status",
        "created_at",
    }
    # No session copy.: no updated_at (an accepted trade).: no job.
    assert {"session_id", "updated_at", "producer_job_id"}.isdisjoint(columns)
    table = _table("model_results")
    # Nullable — a generic CSV trains without a fake feature set. Every
    # modeling run still operates on a defined population.
    assert table.columns["feature_set_id"].nullable is True
    assert table.columns["cohort_id"].nullable is False
    assert table.columns["dataset_digest"].nullable is False
    for column in ("split_definition", "seeds", "environment"):
        # What a refit needs after the.pkl is lost.
        assert table.columns[column].nullable is False, column


def test_a_variant_is_a_natural_key_and_one_is_selected():
    table = _table("model_results")
    uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("created_by_run_id", "comparison_key", "variant_key") in uniques
    partial = next(index for index in table.indexes if index.name == "uq_model_results_one_selected")
    assert partial.unique is True
    assert str(partial.dialect_options["postgresql"]["where"]) == "selected"


def test_the_handoff_from_feature_extraction_is_one_composite_fk():
    table = _table("model_results")
    composites = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.foreign_key_constraints
        if len(constraint.columns) > 1
    }
    # A feature set from another cohort cannot be used.
    assert ("feature_set_id", "cohort_id") in composites
    for constraint in table.foreign_key_constraints:
        assert constraint.ondelete == "RESTRICT", constraint.name
    names = {constraint.name for constraint in table.constraints}
    assert "ck_model_results_input_is_captured" in names  
    assert "ck_model_results_model_file_both_or_neither" in names  


def test_the_migration_ships_the_rules_a_foreign_key_cannot_express():
    migration = _source(_migration_path("j1f0a2c3d4e5"))
    assert 'down_revision: Union[str, Sequence[str], None] = "i1f0a2c3d4e5"' in migration
    # The set is verified frozen under a lock that holds it until commit.
    assert "FOR SHARE" in migration and "frozen feature set" in migration
    # and cohort alignment, shared with steps H and I.
    assert "domain_rows_enforce_producer('modeling_agent')" in migration
    assert "domain_rows_enforce_cohort_alignment" in migration
    # A later evaluation is a new variant, never a rewrite.
    assert "new variant" in migration
    assert "'export_csv'" in migration and "'input_csv'" in migration


def test_the_migration_bounds_match_the_application():
    from application_api.core.json_bounds import DOMAIN_JSON_MAX_BYTES

    spec = importlib.util.spec_from_file_location("_v1_step_j", _migration_path("j1f0a2c3d4e5"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.DOMAIN_JSON_MAX_BYTES == DOMAIN_JSON_MAX_BYTES


def test_only_modeling_writes_model_results_and_replays_do_not_overwrite():
    repository = _source(APPLICATION_ROOT / "repositories/model_results.py")
    assert 'MODELING_AGENT_TYPE = "modeling_agent"' in repository
    assert "require_producer_run" in repository
    assert "reconciliation_required" in repository
    main = _source(APPLICATION_ROOT / "main.py")
    assert "model_results.router" in main


# ----------------------------------------------------------------------
# The writers moved
# ----------------------------------------------------------------------
@pytest.mark.parametrize("tool", ["classification.py", "regression.py"])
def test_the_training_tools_record_rows_not_csvs(tool):
    source = _source(TOOLPACKS_ROOT / "modeling" / tool)
    assert "domain_api.record_model_comparison" in source
    # A replay reads the recorded comparison instead of training again.
    assert "domain_api.get_model_comparison" in source
    assert ".to_csv(" not in source
    for retired in ("model_summary.csv", "model_comparison_results.csv", "_results.csv"):
        assert retired not in source, retired
    # Fitted pipelines are saved only under the model-binary directory.
    assert "results.model_file_base(" in source
    assert "results.file_digest(" in source
    # An unrecognised or swapped file is refused before it is loaded.
    assert "results.model_file_mismatch(" in source
    # A later evaluation is a new variant.
    assert "results.record_independent_evaluation(" in source


def test_a_lost_pipeline_is_refitted_as_a_new_variant_from_verified_input():
    """A lost.pkl is refitted, not reloaded — from verified input, and recorded beside the original rather than over it."""
    source = _source(TOOLPACKS_ROOT / "modeling/refit.py")
    assert "domain_api.record_model_comparison" in source
    assert "__refit_" in source
    assert "dataset_digest" in source and "refusing to refit" in source
    # Registered as a tool (the catalog discovers it; there is no import list).
    assert 'name="pycaret_refit_model"' in source and "@tool(" in source


def test_a_generic_csv_is_captured_not_turned_into_a_fake_feature_set():
    source = _source(TOOLPACKS_ROOT / "modeling/results.py")
    assert 'kind="input_csv"' in source
    assert 'MODEL_BINARY_LOGICAL_ROOT = "modeling/models"' in source
    assert "create_feature_set" not in source


def test_the_reporter_reads_rows_not_files():
    source = _source(TOOLPACKS_ROOT / "reporting/get_modeling_results.py")
    assert "domain_api.model_results_summary" in source
    assert "domain_api.list_feature_sets" in source
    for gone in ("read_csv", "workspace_subpath", "top_10_features.csv", "model_summary.csv"):
        assert gone not in source, gone
    # The caveat a report must never lose.
    assert "NOT a generalisation estimate" in source


def test_the_view_payload_carries_the_comparison_not_file_paths():
    monitor = _source(AGENT_ROOT / "services/pipeline_monitor.py")
    assert '"model_summary"' not in monitor and '"created_files"' not in monitor
    assert '"comparison_key"' in monitor and '"generalisation_estimate"' in monitor
    view = _source(MODELING_VIEW)
    assert "model_summary?:" not in view and "created_files?:" not in view


def test_the_modeling_agent_trains_on_feature_set_ids():
    prompts = _source(AGENT_ROOT / "system_prompts/system_messages.py")
    modeling = prompts.split("MODELING_AGENT_SYSTEM_PROMPT")[1].split("RESULT_AGENT_SYSTEM_PROMPT")[0]
    assert "feature_set_id" in modeling
    assert "models/model_summary.csv" not in modeling
    assert 'browse_workspace(directory="/features")' not in modeling


# ----------------------------------------------------------------------
# The write cutover
# ----------------------------------------------------------------------
def test_the_retired_modeling_paths_raise_but_model_binaries_do_not():
    from storage_paths import HYPOTHESES_DIR
    from storage_write_guard import RETIRED_WORKSPACE_PATHS, RetiredWorkspaceWrite, check_write

    step_j = {pattern for pattern, _, step in RETIRED_WORKSPACE_PATHS if step == "J"}
    assert {
        "outputs/modeling/model_comparison_results.csv",
        "outputs/modeling/model_summary.csv",
    } <= step_j
    workspace = HYPOTHESES_DIR / "workspace_22222222-2222-2222-2222-222222222222"
    for retired in (
        "outputs/modeling/model_summary.csv",
        "outputs/modeling/models/tuned_model_1/tuned_model_1_results.csv",
        "outputs/modeling/models/tuned_model_1/holdout_predictions_tuned_model_1.csv",
        "outputs/modeling/models/tuned_model_1/plots/auc.png",
    ):
        with pytest.raises(RetiredWorkspaceWrite):
            check_write(workspace / retired)
    # exception 2: the fitted pipeline itself is not retired.
    check_write(workspace / "outputs/modeling/models/classification_top_10_abc/tuned_model_1.pkl")
