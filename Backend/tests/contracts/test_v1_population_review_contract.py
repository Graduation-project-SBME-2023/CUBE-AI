"""Contract tests for this migration — population and segmentation review."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APPLICATION_ROOT = BACKEND_ROOT / "application_api"
AGENT_ROOT = BACKEND_ROOT / "agent_system"
MCP_ROOT = BACKEND_ROOT / "mcp"
VERSIONS = APPLICATION_ROOT / "migrations" / "versions"


def _metadata():
    import application_api.db.models  # noqa: F401
    from application_api.db.base import Base

    return Base.metadata


def _table(name: str):
    return _metadata().tables[name]


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _agent_http_source() -> str:
    """The Agent System HTTP layer: app.py plus the api/ package it was split into."""
    paths = [AGENT_ROOT / "app.py", *sorted((AGENT_ROOT / "api").rglob("*.py"))]
    return "\n".join(_source(path) for path in paths)


def _migration(revision: str) -> str:
    for path in VERSIONS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if f'revision: str = "{revision}"' in text:
            return text
    raise AssertionError(f"no migration declares revision {revision}")


def _composite_fks(table_name: str) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in _table(table_name).foreign_key_constraints
        if len(constraint.columns) > 1
    }


def _uniques(table_name: str) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in _table(table_name).constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }


# ----------------------------------------------------------------------
# The tables
# ----------------------------------------------------------------------
def test_cohorts_have_the_a10_shape_without_a_session_or_a_title():
    columns = set(_table("cohorts").columns.keys())
    assert columns == {
        "cohort_id",
        "created_by_run_id",
        "cohort_key",
        "criteria",
        "label_definition",
        "selection_summary",
        "frozen_at",
        "created_at",
    }
    # The session is one indexed join through agent_runs, not a copy.
    assert "session_id" not in columns
    # Frozen_at is the transition that matters.: no title.
    assert "updated_at" not in columns and "title" not in columns
    assert ("created_by_run_id", "cohort_key") in _uniques("cohorts")


def test_cohort_members_carry_the_label_its_source_and_its_evidence():
    columns = set(_table("cohort_members").columns.keys())
    assert {"label", "label_source", "label_confidence", "label_provenance"} <= columns
    # The per-member clinical evidence clinical_data.json used to carry.
    assert "clinical_data" in columns
    # Unlabelled-versus-negative is carried by `label` (null versus false).
    assert "inclusion_status" not in columns
    assert "updated_at" not in columns
    assert ("cohort_id", "volume_series_instance_uid") in _uniques("cohort_members")
    assert ("cohort_id", "ordinal") in _uniques("cohort_members")
    # The tuple the pairs' member FK targets.
    assert ("cohort_id", "member_id", "volume_series_instance_uid") in _uniques("cohort_members")
    # A member's volume must belong to the study it claims.
    assert ("study_uid", "volume_series_instance_uid") in _composite_fks("cohort_members")


def test_a_member_ordinal_key_is_deferrable_so_a_reorder_can_compact():
    constraint = next(
        item
        for item in _table("cohort_members").constraints
        if getattr(item, "name", None) == "uq_cohort_members_cohort_id_ordinal"
    )
    assert constraint.deferrable is True


def test_segmentation_runs_are_cohort_backed_with_a_review_hash():
    columns = set(_table("segmentation_batches").columns.keys())
    assert columns == {
        "segmentation_batch_id",
        "created_by_run_id",
        "cohort_id",
        "batch_key",
        "config",
        "review_status",
        "review_request_hash",
        "reviewed_at",
        "created_at",
    }
    # No cohort mode, and the cohort is never nullable — segmentation
    # cannot run without the population planner's volume list.
    assert "cohort_mode" not in columns
    assert _table("segmentation_batches").columns["cohort_id"].nullable is False
    assert "updated_at" not in columns
    assert ("created_by_run_id", "batch_key") in _uniques("segmentation_batches")
    # The supporting tuple the pairs' composite FK targets.
    assert ("segmentation_batch_id", "cohort_id") in _uniques("segmentation_batches")


def test_segmentation_targets_have_no_created_at_and_no_model_version():
    columns = set(_table("segmentation_targets").columns.keys())
    assert columns == {
        "target_id",
        "segmentation_batch_id",
        "target_key",
        "label",
        "specification",
        "inference_model_id",
    }
    # A target is dated by its parent batch.: no model version/digest.
    assert "created_at" not in columns
    assert {"model_version", "model_digest"}.isdisjoint(columns)


def test_segmentation_pairs_prove_every_alignment_with_a_composite_fk():
    columns = set(_table("segmentation_pairs").columns.keys())
    assert {
        "pair_id",
        "segmentation_batch_id",
        "target_id",
        "volume_series_instance_uid",
        "cohort_id",
        "cohort_member_id",
        "inference_job_id",
        "status",
        "candidate_segmentation_uid",
        "selected_segmentation_uid",
        "segment_number",
        "review_decision",
        "execution_checkpoint",
        "created_at",
    } == columns
    # The pointer to the MONAI receipt, not a mirror of the job files.
    assert "producer_job_id" not in columns
    # The R-ledger drops study_uid: it is reachable through the volume.
    assert "study_uid" not in columns
    assert "updated_at" not in columns
    assert ("target_id", "volume_series_instance_uid") in _uniques("segmentation_pairs")

    composites = _composite_fks("segmentation_pairs")
    assert ("segmentation_batch_id", "target_id") in composites
    assert ("segmentation_batch_id", "cohort_id") in composites
    assert ("cohort_id", "cohort_member_id", "volume_series_instance_uid") in composites
    assert ("candidate_segmentation_uid", "volume_series_instance_uid") in composites
    assert ("selected_segmentation_uid", "volume_series_instance_uid") in composites


def test_review_state_lives_on_the_pair_not_on_the_mask():
    """`segmentations` has no lifecycle column; the decision is here."""
    assert "review_decision" in _table("segmentation_pairs").columns
    assert "review_status" not in _table("segmentation_masks").columns
    assert "review_decision" not in _table("segmentation_masks").columns
    # The supporting tuple the pair's SEG FKs target.
    assert (
        "label_series_instance_uid",
        "volume_series_instance_uid",
    ) in _uniques("segmentation_masks")


def test_every_domain_fk_of_step_h_is_restrict():
    for table in ("cohorts", "cohort_members", "segmentation_batches", "segmentation_targets", "segmentation_pairs"):
        for constraint in _table(table).foreign_key_constraints:
            assert constraint.ondelete == "RESTRICT", f"{table}.{constraint.name}"


def test_the_migration_ships_the_rules_a_foreign_key_cannot_express():
    migration = _migration("h1f0a2c3d4e5")
    for rule in (
        "cohorts_enforce_row_rules",
        "cohort_members_enforce_row_rules",
        "domain_rows_enforce_cohort_alignment",
        "segmentation_runs_enforce_row_rules",
        "segmentation_targets_enforce_immutable",
        "segmentation_pairs_enforce_row_rules",
    ):
        assert rule in migration
    # Freeze on first downstream use.
    assert "UPDATE cohorts SET frozen_at = now()" in migration
    # The undecided-pair case, with its three-valued-logic guard.
    assert "coalesce(" in migration and "IS FALSE" in migration


# ----------------------------------------------------------------------
# The writers moved
# ----------------------------------------------------------------------
def test_the_population_tools_write_the_cohort_not_config_json():
    for name in (
        "population/inclusion_criteria.py",
        "population/validate_anatomy.py",
        "population/cohort_labels.py",
        "targets_matcher.py",
        "save_population.py",
        "database/volumes/search_volumes.py",
    ):
        source = _source(MCP_ROOT / "tools" / name)
        assert "domain_api" in source, name
        # No path building and no writer for the retired files. (The prose in
        # a docstring may still name them — that is what explains the change.)
        assert 'workspace_subpath(workspace_dir, "config.json")' not in source, name
        assert '"cohort_labels.csv"' not in source, name
        assert "populations/targets" not in source, name
        assert "csv.DictWriter" not in source and "csv.writer" not in source, name


def test_extract_cohort_labels_never_invents_a_label():
    """A failed extraction is not a negative finding, and an unmatched clinical record is never assigned to a volume by position."""
    source = _source(MCP_ROOT / "tools" / "population" / "cohort_labels.py")
    assert "positional" not in source.lower()
    assert "patient sex" not in source.lower()
    assert '"has_target_disease": None' in source
    assert "no_clinical_report" in source


def test_the_segmentation_module_checkpoints_into_the_batch():
    module = _source(AGENT_ROOT / "agents/execution_agents/segmentation/module.py")
    assert "SegmentationBatchStore" in module
    assert "workspace_subpath(workspace_dir, \"segmentation_manifest.json\")" not in module
    # The volume list and the instructions come from PostgreSQL.
    assert "get_population_cohort" in module and "get_cohort_analysis_rows" in module
    assert "get_node_run" in module

    store = _source(AGENT_ROOT / "agents/execution_agents/segmentation/batch_store.py")
    # A candidate SEG is only named once the archive row exists.
    assert "candidate_confirmed" in store


def test_the_review_endpoints_read_and_write_rows():
    app = _agent_http_source()
    for gone in (
        "_persist_review_to_disk",
        "_load_review_from_disk",
        "_persist_review_draft_to_disk",
        "review_result.json",
        "review_draft.json",
        "_clean_workspace_population_state",
    ):
        assert gone not in app, gone
    assert "population_review" in app
    # The one final submission publishes the edited masks first.
    assert "publish_modified_mask" in app
    # The wait answers from PostgreSQL, so a restart does not lose a review.
    assert "current_batch(session_id)" in app


def test_an_edited_mask_is_published_as_a_seg_and_never_reshaped_by_guessing():
    services = _source(AGENT_ROOT / "agents/execution_agents/segmentation/services.py")
    assert "async def publish_modified_mask" in services
    assert "refusing to guess an" in services
    app = _agent_http_source()
    assert "Could not infer frontend shape" not in app
    assert "Edited mask size mismatch" in app


def test_feature_extraction_reads_reviewed_pairs_instead_of_another_agents_manifest():
    source = _source(MCP_ROOT / "tools" / "feature_extraction" / "features.py")
    assert "reviewed_masks" in source
    assert 'base_on_workspace_dir("segmentation_manifest.json")' not in source
    assert "cohort_analysis_rows" in source


def test_the_population_handoff_contract_is_checked_against_rows():
    source = _source(AGENT_ROOT / "services" / "population_handoff.py")
    # It reads rows: no workspace path, no file open, no CSV.
    assert "workspace_subpath" not in source
    assert "open(" not in source and "read_text" not in source
    assert "frozen_at" in source and "anatomy_validation" in source
    assert "run_id" in source


def test_the_reuse_gate_promotes_a_pipeline_mask_only_on_a_review_decision():
    source = _source(AGENT_ROOT / "agents/execution_agents/segmentation/resolution.py")
    assert 'review_decision not in {"accepted", "modified"}' in source
    repository = _source(APPLICATION_ROOT / "repositories" / "segmentation_masks.py")
    assert "review_status = 'submitted'" in repository


# ----------------------------------------------------------------------
# The write cutover
# ----------------------------------------------------------------------
def test_the_retired_population_and_review_paths_raise():
    from storage_write_guard import RETIRED_WORKSPACE_PATHS

    patterns = {entry[0] for entry in RETIRED_WORKSPACE_PATHS}
    assert {
        "state/config.json",
        "data/populations/cohort_labels.csv",
        "data/populations/targets/*",
        "state/segmentation_manifest.json",
        "state/review_draft.json",
        "state/review_result.json",
    } <= patterns
    # By path and purpose, never by extension.
    assert all(not pattern.startswith("*.") for pattern in patterns)


def test_the_guard_is_installed_where_agents_and_tools_write():
    assert "install_write_guard()" in _source(AGENT_ROOT / "app.py")
    assert "install_write_guard()" in _source(MCP_ROOT / "server.py")


def test_the_json_bounds_the_migration_writes_match_the_application():
    """A migration CHECK that drifted from the application bound would turn a
    clean 422 into an opaque IntegrityError."""
    import importlib.util

    from application_api.core.json_bounds import CLINICAL_DATA_MAX_BYTES, DOMAIN_JSON_MAX_BYTES

    path = next(
        item for item in VERSIONS.glob("*.py") if "h1f0a2c3d4e5" in item.read_text(encoding="utf-8")
    )
    spec = importlib.util.spec_from_file_location("_v1_step_h_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.DOMAIN_JSON_MAX_BYTES == DOMAIN_JSON_MAX_BYTES
    assert module.CLINICAL_DATA_MAX_BYTES == CLINICAL_DATA_MAX_BYTES


def test_the_application_api_owns_the_domain_routes():
    main = _source(APPLICATION_ROOT / "main.py")
    assert "cohorts.router" in main and "segmentation_batches.router" in main
