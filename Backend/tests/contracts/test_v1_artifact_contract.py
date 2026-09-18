"""Contract tests for artifacts and the MONAI job root."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APPLICATION_ROOT = BACKEND_ROOT / "application_api"
AGENT_ROOT = BACKEND_ROOT / "agent_system"
MONAI_ROOT = BACKEND_ROOT / "monailabel"
VERSIONS = APPLICATION_ROOT / "migrations" / "versions"


def _metadata():
    import application_api.db.models  # noqa: F401
    from application_api.db.base import Base

    return Base.metadata


def _migration(revision: str) -> str:
    for path in VERSIONS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if f'revision: str = "{revision}"' in text:
            return text
    raise AssertionError(f"no migration declares revision {revision}")


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# The table
# ----------------------------------------------------------------------
def test_artifacts_has_the_a13_shape():
    columns = set(_metadata().tables["artifacts"].columns.keys())
    assert {
        "artifact_id",
        "session_id",
        "created_by_agent_run_id",
        "kind",
        "logical_key",
        "display_filename",
        "mime_type",
        "byte_size",
        "sha256",
        "body",
        "render_path",
        "metadata",
        "schema_version",
        "retention_class",
        "created_at",
    } == columns


def test_bytes_live_in_the_row_not_behind_a_storage_key():
    """rejected `body -> storage_key`: a key commits separately from the bytes it names."""
    table = _metadata().tables["artifacts"]
    assert "storage_key" not in table.columns
    assert table.columns["body"].type.__class__.__name__ in {"LargeBinary", "BYTEA"}
    # Nothing collects in current, so no deadline column ships ahead of the
    # sweeper that would read it.
    assert "gc_eligible_at" not in table.columns


def test_one_artifact_per_logical_key_per_session():
    uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in _metadata().tables["artifacts"].constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("session_id", "logical_key") in uniques


def test_the_retention_check_is_a_constraint_not_a_convention():
    checks = {
        constraint.name
        for constraint in _metadata().tables["artifacts"].constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert {
        "ck_artifacts_retention_shape",
        "ck_artifacts_render_path_is_relative",
        "ck_artifacts_byte_size_positive",
        "ck_artifacts_sha256_is_a_digest",
        "ck_artifacts_body_matches_byte_size",
    } <= checks


def test_an_artifacts_run_link_must_belong_to_its_session():
    table = _metadata().tables["artifacts"]
    composite = [
        constraint
        for constraint in table.foreign_key_constraints
        if {column.name for column in constraint.columns}
        == {"created_by_agent_run_id", "session_id"}
    ]
    assert composite, "artifacts needs the (created_by_agent_run_id, session_id) FK"


def test_report_pdfs_and_model_binaries_are_not_artifact_kinds():
    """The PDF is a transient render.: the.pkl stays on disk."""
    migration = _migration("g1f0a2c3d4e5")
    kinds = migration[migration.index("ck_artifacts_kind") : migration.index("ck_artifacts_retention_class")]
    assert "pdf" not in kinds.lower()
    assert "model" not in kinds.lower()


# ----------------------------------------------------------------------
# The read path
# ----------------------------------------------------------------------
def test_binary_reads_use_their_own_bounded_pool():
    source = _source(APPLICATION_ROOT / "db" / "binary_session.py")
    assert "max_overflow=BINARY_POOL_MAX_OVERFLOW" in source
    assert "statement_timeout" in source
    assert "Semaphore" in source

    limits = _source(APPLICATION_ROOT / "core" / "artifact_limits.py")
    assert "BINARY_READ_CONCURRENCY" in limits
    # The bound is load-bearing, and the reason is written where the number is.
    assert "memory ceiling" in limits


def test_saturation_is_a_bounded_503_with_retry_after():
    source = _source(APPLICATION_ROOT / "api" / "routes" / "artifacts.py")
    assert "status_code=503" in source
    assert "Retry-After" in source


def test_listing_artifacts_never_selects_the_body():
    """A `SELECT *` for a listing drags every body TOAST would keep out."""
    source = _source(APPLICATION_ROOT / "repositories" / "artifacts.py")
    listing = source[source.index("async def list_artifacts") : source.index("async def get_artifact")]
    assert "_CATALOGUE_COLUMNS" in listing
    assert "Artifact.body" not in listing

    catalogue = source[source.index("_CATALOGUE_COLUMNS = (") : source.index("async def list_artifacts")]
    assert "Artifact.body" not in catalogue


def test_same_key_identical_bytes_is_reuse_and_different_bytes_is_a_conflict():
    source = _source(APPLICATION_ROOT / "repositories" / "artifacts.py")
    assert "_reuse_or_conflict" in source
    assert "status_code=409" in source
    # Zero length is a failed write that looks successful.
    assert "zero bytes" in source


# ----------------------------------------------------------------------
# The exception: GIF renders
# ----------------------------------------------------------------------
def test_gif_overlays_are_catalogued_as_renders_not_uploaded():
    client = _source(AGENT_ROOT / "services" / "artifact_client.py")
    assert "register_render" in client
    assert "upload_bytes" in client
    # The asymmetry is the point: a failed retained write fails the operation,
    # a failed catalogue write does not fail the render.
    assert "raise RuntimeError" in client

    module = _source(
        AGENT_ROOT / "agents" / "execution_agents" / "segmentation" / "module.py"
    )
    assert "register_render" in module
    app = _source(AGENT_ROOT / "app.py")
    assert "register_render" in app


def test_render_paths_are_resolved_through_storage_paths():
    source = _source(APPLICATION_ROOT / "repositories" / "artifacts.py")
    assert "from storage_paths import" in source
    assert "escapes the session workspace" in source

    dockerfile = (BACKEND_ROOT.parent / "deploy" / "Dockerfile.application-api").read_text(
        encoding="utf-8"
    )
    assert "COPY Backend/storage_paths.py" in dockerfile
    assert "python-multipart" in dockerfile


# ----------------------------------------------------------------------
# — the MONAI job root stays on disk and is authoritative
# ----------------------------------------------------------------------
def test_the_job_root_has_one_owner():
    source = _source(MONAI_ROOT / "services" / "job_root.py")
    assert "class JobRootLock" in source
    assert "LOCK_EX" in source and "LOCK_NB" in source

    for endpoint in ("inference_jobs.py", "training_jobs.py"):
        text = _source(MONAI_ROOT / "endpoints" / endpoint)
        # The lock is taken BEFORE the startup scan that marks jobs INTERRUPTED.
        assert "claim_job_root()" in text
        scan = text[text.index("def interrupt_stale_jobs") :]
        assert scan.index("claim_job_root()") < scan.index("glob(")


def test_job_metadata_is_written_atomically_with_an_fsync():
    source = _source(MONAI_ROOT / "services" / "job_root.py")
    assert "os.fsync" in source
    assert "os.replace" in source

    for endpoint in ("inference_jobs.py", "training_jobs.py"):
        text = _source(MONAI_ROOT / "endpoints" / endpoint)
        assert "write_json_atomically" in text
        assert "temporary_path.replace(metadata_path)" not in text
        assert "temporary.replace(path)" not in text


def test_a_terminal_job_keeps_its_outcome():
    source = _source(MONAI_ROOT / "services" / "job_root.py")
    assert "TERMINAL_STATUSES" in source
    assert "guard_terminal" in source

    inference = _source(MONAI_ROOT / "endpoints" / "inference_jobs.py")
    assert "JobStateConflict" in inference
    # Cancelling a finished job is a no-op, not a rewrite.
    assert "already finished is a no-op" in inference


def test_purge_frees_the_result_before_the_job_id():
    inference = _source(MONAI_ROOT / "endpoints" / "inference_jobs.py")
    purge = inference[inference.index("if purge:") :]
    assert purge.index("result_path.unlink") < purge.index("metadata_path.unlink")


def test_training_is_serialised_per_workflow_as_well_as_per_job():
    training = _source(MONAI_ROOT / "endpoints" / "training_jobs.py")
    assert "_workflow_lock(" in training
    assert "async with _workflow_lock(request.task_name)" in training


def test_monai_still_owns_no_database_access():
    """is operational work on the job folder — nothing moves into SQL."""
    source = _source(MONAI_ROOT / "services" / "job_root.py")
    assert "sqlalchemy" not in source.lower()
    assert "DATABASE_URL" not in source
