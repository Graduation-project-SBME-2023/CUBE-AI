"""Contract tests for the current physical schema."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APPLICATION_ROOT = BACKEND_ROOT / "application_api"
VERSIONS = APPLICATION_ROOT / "migrations" / "versions"


def _metadata():
    import application_api.db.models  # noqa: F401  (registers every mapper)
    from application_api.db.base import Base

    return Base.metadata


def _fk_actions(table_name: str) -> dict[str, str]:
    table = _metadata().tables[table_name]
    return {
        fk.parent.name: (fk.ondelete or "NO ACTION").upper()
        for fk in table.foreign_keys
    }


# ----------------------------------------------------------------------
# Migration chain
# ----------------------------------------------------------------------
def test_migration_chain_has_exactly_one_head():
    """A branched chain makes `alembic upgrade head` ambiguous and the
    readiness probe's version report meaningless."""
    revisions: dict[str, str] = {}
    referenced: set[str] = set()
    for path in VERSIONS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        revision = None
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("revision") and "=" in stripped and revision is None:
                revision = stripped.split("=", 1)[1].strip().strip("'\"")
            elif stripped.startswith("down_revision") and "=" in stripped:
                value = stripped.split("=", 1)[1].strip()
                if value.startswith("("):
                    for part in value.strip("()").split(","):
                        part = part.strip().strip("'\"")
                        if part:
                            referenced.add(part)
                elif value not in ("None",):
                    referenced.add(value.strip("'\""))
        assert revision, f"{path.name} declares no revision id"
        revisions[revision] = path.name

    heads = set(revisions) - referenced
    assert len(heads) == 1, f"expected one head, found {sorted(heads)}"


@pytest.mark.parametrize(
    "revision",
    ["b1f0a2c3d4e5", "c1f0a2c3d4e5", "d1f0a2c3d4e5", "e1f0a2c3d4e5"],
)
def test_v1_revision_is_present_and_reversible(revision: str):
    """Every current revision ships an upgrade AND a downgrade body."""
    matches = [p for p in VERSIONS.glob("*.py") if revision in p.read_text(encoding="utf-8")[:2000]]
    assert matches, f"no migration declares revision {revision}"
    source = next(p for p in matches).read_text(encoding="utf-8")
    assert "def upgrade()" in source
    assert "def downgrade()" in source
    # A downgrade body that is only `pass` is a lie about reversibility.
    downgrade_body = source.split("def downgrade()", 1)[1]
    assert downgrade_body.strip().splitlines()[1:], "downgrade() is empty"


# ----------------------------------------------------------------------
# Step B — imaging safety
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "table", ["patients", "studies", "volume_series", "segmentations"]
)
def test_archive_tables_carry_archive_status(table: str):
    """Orthanc loss marks a row, it never deletes scientific history. is why `segmentations` is in this list."""
    columns = _metadata().tables[table].columns
    assert "archive_status" in columns
    assert columns["archive_status"].nullable is False


@pytest.mark.parametrize(
    "table,column",
    [
        ("studies", "patient_id"),
        ("volume_series", "study_uid"),
        ("segmentations", "volume_series_instance_uid"),
        ("segmentations", "model_id"),
        ("radiomics_features", "label_series_instance_uid"),
        ("workflow_details", "task_name"),
        ("workflow_details", "label_series_instance_uid"),
    ],
)
def test_research_dicom_foreign_keys_are_restrict(table: str, column: str):
    """Every research/history DICOM FK is RESTRICT."""
    assert _fk_actions(table)[column] == "RESTRICT"


def test_segmentations_columns_match_the_v1_shape():
    columns = set(_metadata().tables["segmentations"].columns.keys())
    # Renamed (the old names described neither the content nor the provenance).
    assert "series_description" in columns and "segmentation_type" not in columns
    assert "generation_method" in columns and "method" not in columns
    # Added: origin is what closes the "every synced SEG is reusable" bug.
    assert {"origin", "is_empty_mask", "model_id"} <= columns
    # Dropped only after model_id was backfilled from the exact name.
    assert "name" not in columns
    # Dropped: no producer ever wrote them, no consumer ever read them (V1 plan #14).
    assert "provenance" not in columns and "content_digest" not in columns
    # /: neither value exists to record, so neither column exists.
    assert "producing_model_version" not in columns
    assert "producing_model_digest" not in columns
    # Review state lives on segmentation_pairs, not here.
    assert "review_status" not in columns and "pacs_status" not in columns


def test_models_gains_no_columns_without_a_producer():
    """MONAI's /info/ returns type, labels, dimension, description and config — nothing that could populate a version or a weights digest."""
    columns = set(_metadata().tables["models"].columns.keys())
    assert "updated_at" in columns  # — the sync writer mutates the row
    for dead in ("current_version", "weights_digest", "weights_registered_at", "status"):
        assert dead not in columns


def test_volume_series_supporting_unique_tuple_exists():
    """The tuple every membership/composite FK targets."""
    table = _metadata().tables["volume_series"]
    tuples = {
        tuple(sorted(c.name for c in constraint.columns))
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("study_uid", "volume_series_instance_uid") in tuples


def test_workflow_carries_dataset_id_and_freeze_state():
    """The nnUNet dataset id is allocated under this UNIQUE constraint."""
    table = _metadata().tables["workflow"]
    assert table.columns["dataset_id"].nullable is False
    assert table.columns["dataset_id"].unique is True
    assert "frozen_at" in table.columns
    assert "definition" in table.columns
    # REJECTED removal — the training-setup UI reads display_title.
    assert "display_title" in table.columns


def test_workflow_details_orders_training_input_deterministically():
    """Training input is all frozen members in deterministic order."""
    table = _metadata().tables["workflow_details"]
    assert table.columns["case_index"].nullable is False
    assert table.columns["channel_index"].nullable is False
    # subject_key: review proposed removing it; REJECTED — it is the
    # patient-level split group.
    assert "subject_key" in table.columns
    tuples = {
        tuple(c.name for c in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("task_name", "case_index", "channel_index") in tuples


def test_training_start_reads_every_member_in_order():
    """The previous implementation capped membership at an unsorted
    `LIMIT 1000`, so a large task trained on an arbitrary subset."""
    source = (APPLICATION_ROOT / "api" / "routes" / "workflow.py").read_text(
        encoding="utf-8"
    )
    start = source.index("async def initialize_training")
    body = source[start : source.index("@router.get", start)]
    assert ".limit(" not in body, "training input must not be paginated"
    assert "order_by(WorkflowDetail.case_index, WorkflowDetail.channel_index)" in body
    assert "frozen_at" in body, "admitting a training job must freeze the workflow"


def test_orthanc_delete_marks_unavailable_instead_of_deleting():
    """current +: deleting a study or SEG in Orthanc marks it unavailable and preserves the radiomics rows and reports referencing it."""
    sync_routes = (APPLICATION_ROOT / "api" / "routes" / "sync.py").read_text(
        encoding="utf-8"
    )
    for helper in (
        "mark_patient_unavailable",
        "mark_study_unavailable",
        "mark_series_unavailable",
        "mark_segmentation_unavailable",
    ):
        assert helper in sync_routes
    for removed in (
        "delete_patient_from_db",
        "delete_study_from_db",
        "delete_series_from_db",
        "delete_segmentation_from_db",
    ):
        assert removed not in sync_routes

    repository = (APPLICATION_ROOT / "repositories" / "orthanc_sync.py").read_text(
        encoding="utf-8"
    )
    assert "async def delete_patient_from_db" not in repository
    assert "async def mark_patient_unavailable" in repository


def test_orthanc_sync_never_writes_segmentation_origin_as_a_producer():
    """The sync knows only that a SEG appeared; it must record `imported` and
    must never overwrite a producer's declaration."""
    repository = (APPLICATION_ROOT / "repositories" / "orthanc_sync.py").read_text(
        encoding="utf-8"
    )
    assert 'origin="imported"' in repository
    # The update branch must not touch origin or is_empty_mask at all.
    start = repository.index("async def _get_or_create_segmentation")
    end = repository.index("async def _fetch_first_instance_tags")
    update_branch = repository[repository.index("updated = False", start) : end]
    assert "seg.origin" not in update_branch
    assert "seg.is_empty_mask" not in update_branch
