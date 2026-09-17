"""Ingestion and mask storage must respect the system's service boundaries.

The Application API is the sole PostgreSQL owner.  A series or a segmentation
therefore reaches the database the way a DICOM SEG already does -- through
Orthanc and the sync webhook -- and never by the agent system opening its own
connection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = BACKEND_ROOT / "agent_system"
INGESTION_ROOT = AGENT_ROOT / "ingestion"
MASK_STORE = AGENT_ROOT / "services" / "mask_store.py"

FORBIDDEN_IMPORTS = (
    "DATABASE_URL",
    "sqlalchemy",
    "asyncpg",
    "application_api",
    "alembic",
)


def _sources() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in INGESTION_ROOT.glob("*.py")
    }


@pytest.mark.contract
def test_ingestion_never_reaches_the_database_directly():
    for name, source in _sources().items():
        for forbidden in FORBIDDEN_IMPORTS:
            assert forbidden not in source, (
                f"{name} references {forbidden}; ingested series must reach the "
                "database through Orthanc and /sync/orthanc-webhook"
            )


@pytest.mark.contract
def test_ingestion_registers_series_through_the_sync_webhook():
    source = (INGESTION_ROOT / "service.py").read_text(encoding="utf-8")

    assert "/sync/orthanc-webhook" in source
    assert "stable_series" in source


@pytest.mark.contract
def test_conversion_stays_free_of_transport_concerns():
    # The geometry is the part that fails silently, so it has to remain unit
    # testable without Orthanc, FastAPI or a workspace.
    source = (INGESTION_ROOT / "nifti_to_dicom.py").read_text(encoding="utf-8")

    for forbidden in ("httpx", "fastapi", "OrthancClient", "aiohttp", "requests"):
        assert forbidden not in source, (
            f"nifti_to_dicom.py references {forbidden}; the converter must stay "
            "a pure function"
        )


@pytest.mark.contract
def test_ingested_uids_stay_on_their_own_branch():
    from ingestion.provenance import NIFTI_UID_ROOT, is_ingested_uid

    # A prefix test is what lets "was this converted?" be answered in SQL or in
    # an Orthanc query without reading any tags.
    assert NIFTI_UID_ROOT.startswith("1.2.826.0.1.3680043.10.511.")
    assert NIFTI_UID_ROOT != "1.2.826.0.1.3680043.10.511.3."  # the SEG branch
    assert is_ingested_uid(NIFTI_UID_ROOT + "1.2.3")
    assert not is_ingested_uid("1.2.826.0.1.3680043.10.511.3.9")
    assert not is_ingested_uid(None)


@pytest.mark.contract
def test_mask_store_never_reaches_the_database_directly():
    source = MASK_STORE.read_text(encoding="utf-8")

    for forbidden in FORBIDDEN_IMPORTS:
        assert forbidden not in source, (
            f"mask_store.py references {forbidden}; segmentations are registered "
            "by Orthanc's sync hook, not by the agent system"
        )


@pytest.mark.contract
def test_mask_cache_lives_in_the_disposable_tree():
    import re

    source = MASK_STORE.read_text(encoding="utf-8")

    # cache/ and temp/ are the only trees a session teardown may delete, so
    # every workspace path the store resolves must be the mask cache -- never a
    # path under data/ holding real study artifacts.
    assert 'MASK_CACHE = "mask_cache"' in source
    workspace_paths = re.findall(r"ws_sub\([^)]*\)", source)
    assert workspace_paths, "mask_store must resolve workspace paths through ws_sub"
    assert all("MASK_CACHE" in call for call in workspace_paths), workspace_paths


@pytest.mark.contract
def test_storage_layout_registers_the_ingestion_locations():
    import storage_paths

    # Job records and staged uploads are disposable, so they must live under the
    # runtime roots rather than beside a session's study data.
    assert storage_paths.INGEST_JOBS_DIR.name == "ingestion"
    assert storage_paths.INGEST_JOBS_DIR.parent.name == "jobs"
    assert storage_paths._WORKSPACE_DIR_MAP["mask_cache"] == ("cache", "masks")
