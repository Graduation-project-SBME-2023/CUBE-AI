"""Contract tests for this migration — feature extraction."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
APPLICATION_ROOT = BACKEND_ROOT / "application_api"
MCP_ROOT = BACKEND_ROOT / "mcp"
AGENT_ROOT = BACKEND_ROOT / "agent_system"
VERSIONS = APPLICATION_ROOT / "migrations" / "versions"
FEATURES_VIEW = (
    REPO_ROOT
    / "Frontend/OHIF-Viewer/extensions/research-playground/src/components/PlaygroundCanvas/views"
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


def _uniques(table: str) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in _table(table).constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }


def _composite_fks(table: str) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in _table(table).foreign_key_constraints
        if len(constraint.columns) > 1
    }


# ----------------------------------------------------------------------
# radiomics_features
# ----------------------------------------------------------------------
def test_radiomics_features_have_the_v1_provenance_columns():
    columns = set(_table("radiomics_features").columns.keys())
    assert columns == {
        "id",
        "session_id",
        "label_series_instance_uid",
        "source_volume_uid",
        "segment_number",
        "segmentation_pair_id",
        "created_by_agent_run_id",
        "features",
        "extractor_name",
        "extractor_version",
        "configuration",
        "config_digest",
        "image_digest",
        "segmentation_digest",
        "source_digest",
        "schema_version",
        "created_at",
    }
    # No vector_digest and no
    # quality_flags (features.py discards every diagnostics_ key).: no
    # updated_at.: the run, not a job.
    assert {"vector_digest", "quality_flags", "updated_at", "producer_job_id"}.isdisjoint(columns)


def test_only_the_run_link_is_nullable_for_a_new_row():
    """/: `created_by_agent_run_id` stays nullable in current for legacy and new rows alike; every other provenance field is required."""
    table = _table("radiomics_features")
    assert table.columns["created_by_agent_run_id"].nullable is True
    assert table.columns["segmentation_pair_id"].nullable is True
    for column in (
        "session_id",
        "label_series_instance_uid",
        "source_volume_uid",
        "segment_number",
        "extractor_name",
        "extractor_version",
        "configuration",
        "config_digest",
        "image_digest",
        "segmentation_digest",
        "source_digest",
        "schema_version",
    ):
        assert table.columns[column].nullable is False, column


def test_the_extraction_is_content_addressed():
    assert (
        "session_id",
        "label_series_instance_uid",
        "segment_number",
        "config_digest",
        "source_digest",
    ) in _uniques("radiomics_features")
    composites = _composite_fks("radiomics_features")
    # The measured SEG is a mask of the named source volume.
    assert ("label_series_instance_uid", "source_volume_uid") in composites
    # The producing run is a run of the row's session.
    assert ("created_by_agent_run_id", "session_id") in composites


# ----------------------------------------------------------------------
# feature_sets
# ----------------------------------------------------------------------
def test_feature_sets_have_the_a14_shape():
    columns = set(_table("feature_sets").columns.keys())
    assert columns == {
        "feature_set_id",
        "created_by_agent_run_id",
        "cohort_id",
        "set_key",
        "schema_version",
        "feature_names",
        "feature_matrix",
        "source_feature_ids",
        "selection",
        "extraction_config",
        "analysis",
        "quality_summary",
        "config_digest",
        "source_digest",
        "manifest_digest",
        "status",
        "frozen_at",
        "created_at",
    }
    # /// removals.
    assert {"session_id", "cohort_mode", "updated_at", "producer_job_id"}.isdisjoint(columns)
    table = _table("feature_sets")
    # A feature set cannot be created without a cohort.
    assert table.columns["cohort_id"].nullable is False
    assert table.columns["selection"].nullable is False
    assert ("created_by_agent_run_id", "set_key") in _uniques("feature_sets")
    # The supporting tuple model_results' composite FK targets.
    assert ("feature_set_id", "cohort_id") in _uniques("feature_sets")


def test_there_is_no_vectors_table():
    import application_api.db.models  # noqa: F401
    from application_api.db.base import Base

    assert "feature_set_vectors" not in Base.metadata.tables
    assert "jobs" not in Base.metadata.tables


def test_rectangularity_and_label_supervision_are_constraints():
    names = {constraint.name for constraint in _table("feature_sets").constraints}
    assert "ck_feature_sets_feature_matrix_shape" in names
    assert "ck_feature_sets_selection_states_label_supervision" in names
    assert "ck_feature_sets_frozen_at_iff_frozen" in names


def test_every_step_i_fk_is_restrict():
    for table in ("radiomics_features", "feature_sets"):
        for constraint in _table(table).foreign_key_constraints:
            assert constraint.ondelete == "RESTRICT", f"{table}.{constraint.name}"


# ----------------------------------------------------------------------
# The migration
# ----------------------------------------------------------------------
def test_the_migration_ships_the_rules_a_foreign_key_cannot_express():
    migration = _source(_migration_path("i1f0a2c3d4e5"))
    assert 'down_revision: Union[str, Sequence[str], None] = "h1f0a2c3d4e5"' in migration
    for rule in (
        "feature_matrix_is_rectangular",
        "feature_sets_enforce_row_rules",
        "radiomics_features_enforce_row_rules",
        "domain_rows_enforce_producer",
        "domain_rows_enforce_cohort_alignment",
    ):
        assert rule in migration, rule
    # Legacy rows are exempted by NOT VALID, never given invented values.
    assert "NOT VALID" in migration
    assert "never invented" in migration
    # A legacy source row can never enter a frozen set.
    assert "f.source_digest IS NULL" in migration
    # for feature extraction.
    assert "domain_rows_enforce_producer('feature_extraction_agent')" in migration


def test_the_migration_bounds_match_the_application():
    from application_api.core.json_bounds import DOMAIN_JSON_MAX_BYTES, FEATURE_MATRIX_MAX_BYTES

    spec = importlib.util.spec_from_file_location("_v1_step_i", _migration_path("i1f0a2c3d4e5"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.DOMAIN_JSON_MAX_BYTES == DOMAIN_JSON_MAX_BYTES
    assert module.FEATURE_MATRIX_MAX_BYTES == FEATURE_MATRIX_MAX_BYTES


def test_the_api_and_the_tool_compute_the_same_source_digest():
    """The API recomputes a set's source_digest from its rows; it must be the
    exact function the extraction tool uses."""
    from application_api.repositories.feature_sets import rows_source_digest
    from tests.support.mcp_loader import load_mcp_tool

    tool = load_mcp_tool("feature_extraction.digests")
    sample = ["c" * 64, "a" * 64, "b" * 64]
    assert rows_source_digest(sample) == tool.rows_source_digest(sample)


# ----------------------------------------------------------------------
# The writer
# ----------------------------------------------------------------------
def test_the_extraction_tool_records_rows_with_their_digests():
    source = _source(MCP_ROOT / "tools/feature_extraction/features.py")
    assert "domain_api.record_radiomics_rows" in source
    for digest in ("image_digest", "segmentation_digest", "config_digest", "source_digest"):
        assert f"provenance.{digest}(" in source, digest
    # Masks without a SEG identity cannot be measured: the fallback is gone.
    assert "segmentation_batches" not in source
    assert '"target_id": "roi"' not in source


def test_the_selection_tool_freezes_feature_sets_instead_of_writing_csvs():
    source = _source(MCP_ROOT / "tools/feature_extraction/features.py")
    assert "domain_api.create_feature_set" in source
    assert "domain_api.freeze_feature_set" in source
    for retired in (
        "radiomics_features.csv",
        "top_{n}_features.csv",
        "feature_selection_split.json",
        "feature_importance.csv",
        "categorical_encodings.json",
        "organ_availability_audit.csv",
        "feature_schema.json",
    ):
        assert retired not in source, retired
    assert ".to_csv(" not in source
    # The split group is the cohort's subject key.
    assert "detect_group_column" not in source
    assert "group_column = SUBJECT_KEY" in source


def test_the_extraction_cache_is_content_addressed():
    source = _source(MCP_ROOT / "tools/feature_extraction/cache_utils.py")
    assert "st_mtime" not in source
    assert "def get_cache_key(image_digest: str, segmentation_digest: str, config_digest: str)" in source


def test_radiomics_rows_cannot_be_created_without_provenance_or_deleted():
    routes = _source(APPLICATION_ROOT / "api/routes/radiomics.py")
    assert '@router.post("/", ' not in routes
    assert "@router.delete" not in routes
    assert '@router.post("/run/{agent_run_id}/batch"' in routes
    repository = _source(APPLICATION_ROOT / "repositories/radiomics.py")
    assert "reconciliation_required" in repository
    assert "FEATURE_EXTRACTION_AGENT_TYPE" in repository


def test_feature_set_provenance_is_derived_not_asserted():
    repository = _source(APPLICATION_ROOT / "repositories/feature_sets.py")
    assert "rows_source_digest(" in repository
    assert "_stored_manifest_digest" in repository
    assert "reconciliation_required" in repository


def test_the_application_api_owns_the_step_i_routes():
    main = _source(APPLICATION_ROOT / "main.py")
    assert "feature_sets.router" in main and "radiomics.router" in main


# ----------------------------------------------------------------------
# The readers moved
# ----------------------------------------------------------------------
def test_the_feature_view_downloads_from_the_application_api():
    dropdown = _source(FEATURES_VIEW / "features/DownloadDropdown.tsx")
    assert "BACKEND_API" in dropdown
    assert "/api/workspace/file" not in dropdown
    view = _source(FEATURES_VIEW / "FeaturesView.tsx")
    assert "csv_paths" not in view


def test_the_view_emitter_forwards_rows_and_sets_not_paths():
    monitor = _source(AGENT_ROOT / "services/pipeline_monitor.py")
    assert '"csv_paths"' not in monitor
    assert '"result_files"' not in monitor
    assert '"feature_sets"' in monitor


def test_the_agent_is_told_there_are_no_feature_files():
    prompts = _source(AGENT_ROOT / "system_prompts/system_messages.py")
    extraction = prompts.split("MODELING_AGENT_SYSTEM_PROMPT")[0]
    assert "features/top_10_features.csv" not in extraction
    assert "features/radiomics_features.csv" not in extraction
    assert "WITHOUT input_path" in extraction


# ----------------------------------------------------------------------
# The write cutover
# ----------------------------------------------------------------------
def test_the_retired_feature_paths_raise():
    from storage_paths import HYPOTHESES_DIR
    from storage_write_guard import RETIRED_WORKSPACE_PATHS, RetiredWorkspaceWrite, check_write

    step_i = {pattern for pattern, _, step in RETIRED_WORKSPACE_PATHS if step == "I"}
    assert {
        "outputs/features/top_*_features*.csv",
        "outputs/features/feature_selection_split.json",
        "outputs/features/radiomics_features.csv",
    } <= step_i
    assert all(not pattern.startswith("*.") for pattern in step_i)

    workspace = HYPOTHESES_DIR / "workspace_11111111-1111-1111-1111-111111111111"
    with pytest.raises(RetiredWorkspaceWrite):
        check_write(workspace / "outputs/features/top_10_features.csv")
    with pytest.raises(RetiredWorkspaceWrite):
        check_write(workspace / "outputs/features/plots/feature_importance_plot.png")
    # By path and purpose, never by extension: a CSV elsewhere is fine.
    check_write(workspace / "data/volumes/volume.csv")
