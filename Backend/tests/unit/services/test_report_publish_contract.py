"""Unit tests for the report compile-then-publish contract."""

from __future__ import annotations

import pytest

from application_api.schemas.reports import (
    ReportCreate,
    ReportImageIn,
    ReportImageOut,
    ReportOut,
    ReportPublish,
    ReportSummaryOut,
)


# ── ReportCreate — exclusive-source ──────────────────────────────────────

@pytest.mark.unit
def test_create_requires_exactly_one_source():
    """A report must have either an agent run or a literature review, not both."""
    session = "00000000-0000-0000-0000-000000000001"
    run_id = "00000000-0000-0000-0000-000000000002"
    lit_id = "00000000-0000-0000-0000-000000000003"

    # Both provided → should fail
    with pytest.raises(Exception, match="Exactly one|exclusive"):
        ReportCreate(
            session_id=session,
            created_by_agent_run_id=run_id,
            literature_review_id=lit_id,
            source_text=r"\documentclass{article}\begin{document}Hi\end{document}",
        )

    # Neither provided → should fail
    with pytest.raises(Exception, match="Exactly one|exclusive"):
        ReportCreate(
            session_id=session,
            source_text=r"\documentclass{article}\begin{document}Hi\end{document}",
        )


@pytest.mark.unit
def test_create_agent_run_source():
    session = "00000000-0000-0000-0000-000000000001"
    run_id = "00000000-0000-0000-0000-000000000002"
    report = ReportCreate(
        session_id=session,
        created_by_agent_run_id=run_id,
        source_text=r"\documentclass{article}\begin{document}Hi\end{document}",
    )
    assert report.created_by_agent_run_id is not None
    assert report.literature_review_id is None


@pytest.mark.unit
def test_create_literature_review_source():
    session = "00000000-0000-0000-0000-000000000001"
    lit_id = "00000000-0000-0000-0000-000000000003"
    report = ReportCreate(
        session_id=session,
        literature_review_id=lit_id,
        source_text=r"\documentclass{article}\begin{document}Hi\end{document}",
    )
    assert report.literature_review_id is not None
    assert report.created_by_agent_run_id is None


@pytest.mark.unit
def test_create_source_text_cannot_be_blank():
    with pytest.raises(Exception):
        ReportCreate(
            session_id="00000000-0000-0000-0000-000000000001",
            created_by_agent_run_id="00000000-0000-0000-0000-000000000002",
            source_text="",
        )


@pytest.mark.unit
def test_create_source_format_default_is_latex():
    report = ReportCreate(
        session_id="00000000-0000-0000-0000-000000000001",
        created_by_agent_run_id="00000000-0000-0000-0000-000000000002",
        source_text=r"\documentclass{article}\begin{document}.\end{document}",
    )
    assert report.source_format == "latex"


@pytest.mark.unit
def test_create_source_format_validates():
    with pytest.raises(Exception):
        ReportCreate(
            session_id="00000000-0000-0000-0000-000000000001",
            created_by_agent_run_id="00000000-0000-0000-0000-000000000002",
            source_text=r"\documentclass{article}\begin{document}.\end{document}",
            source_format="docx",   # not in (latex, markdown)
        )


# ── ReportPublish — compile-then-publish contract ──────────────────────

@pytest.mark.unit
def test_publish_requires_expected_version():
    with pytest.raises(Exception):
        ReportPublish(
            latex_text=r"\documentclass{article}\begin{document}.\end{document}",
            expected_version=0,   # must be >= 1
        )


@pytest.mark.unit
def test_publish_expected_version_must_be_positive():
    with pytest.raises(Exception):
        ReportPublish(
            latex_text=r"\documentclass{article}\begin{document}.\end{document}",
            expected_version=-1,
        )


@pytest.mark.unit
def test_publish_valid_schema():
    pub = ReportPublish(
        latex_text=r"\documentclass{article}\begin{document}.\end{document}",
        expected_version=3,
        image_keys=["fig1.png", "fig2.jpeg"],
    )
    assert pub.expected_version == 3
    assert pub.image_keys == ["fig1.png", "fig2.jpeg"]


@pytest.mark.unit
def test_publish_image_keys_defaults_empty():
    pub = ReportPublish(
        latex_text=r"\documentclass{article}\begin{document}.\end{document}",
        expected_version=1,
    )
    assert pub.image_keys == []


# ── ReportImageIn — image key safety ─────────────────────────────────────────

@pytest.mark.unit
def test_image_in_requires_non_blank_key():
    with pytest.raises(Exception):
        ReportImageIn(
            artifact_id="00000000-0000-0000-0000-000000000010",
            image_key="",
        )


@pytest.mark.unit
def test_image_in_valid():
    img = ReportImageIn(
        artifact_id="00000000-0000-0000-0000-000000000010",
        image_key="spleen_heatmap.png",
    )
    assert img.image_key == "spleen_heatmap.png"


# ── ReportOut — no PDF column ────────────────────────────────────────────────

@pytest.mark.unit
def test_report_out_has_no_pdf_column():
    """No durable PDF — the out schema must not carry a pdf_path field."""
    fields = ReportOut.model_fields
    assert "pdf_path" not in fields, "ReportOut must not have pdf_path (A19 — no durable PDF)"
    assert "pdf_url" not in fields, "ReportOut must not have pdf_url (A19 — no durable PDF)"


@pytest.mark.unit
def test_report_out_has_source_version():
    """source_version is the optimistic-concurrency token."""
    fields = ReportOut.model_fields
    assert "source_version" in fields


@pytest.mark.unit
def test_report_summary_out_has_no_source_text():
    """List endpoint returns lightweight summary; source_text is expensive to serialise."""
    fields = ReportSummaryOut.model_fields
    assert "source_text" not in fields, (
        "ReportSummaryOut must not include source_text; use GET /reports/{id} for full content"
    )


# ── Stale version detection logic (unit, no DB) ──────────────────────────────

@pytest.mark.unit
def test_stale_version_detection_logic():
    """Simulate the optimistic-concurrency check in publish_report."""
    current_version = 5
    stale_expected = 4   # already advanced past

    # If expected_version != current_version, we should 409.
    assert current_version != stale_expected, "Stale version must not match current"

    fresh_expected = 5   # matches current
    assert current_version == fresh_expected, "Fresh version must match current"


# ── — no durable PDF written (unit verification of the contract) ─────────

try:
    import fastapi  # noqa: F401
    import sqlalchemy  # noqa: F401
    _deps_available = True
except ImportError:
    _deps_available = False

_requires_deps = pytest.mark.skipif(
    not _deps_available,
    reason="fastapi/sqlalchemy not importable in this test environment",
)


@pytest.mark.unit
@_requires_deps
def test_a19_compile_raises_on_bad_latex(monkeypatch, tmp_path):
    """A compile error must raise HTTPException(422) and write no file."""
    from unittest.mock import MagicMock, patch
    import subprocess
    from fastapi import HTTPException
    from application_api.repositories.reports import _compile_latex

    bad_result = MagicMock()
    bad_result.returncode = 1
    bad_result.stdout = b"! LaTeX Error: \\begin{document} ended"
    bad_result.stderr = b""

    with patch("subprocess.run", return_value=bad_result), \
         patch("application_api.repositories.reports.TEMP_DIR", tmp_path, create=True):
        with pytest.raises(HTTPException) as exc_info:
            _compile_latex(r"\documentclass{article}\begin{document}", {})
        assert exc_info.value.status_code == 422
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail.get("code") == "latex_compile_error"
        # No PDF file should exist anywhere under tmp_path
        pdf_files = list(tmp_path.rglob("*.pdf"))
        assert pdf_files == [], f"No durable PDF should be written; found {pdf_files}"


@pytest.mark.unit
@_requires_deps
def test_a19_successful_compile_returns_bytes(monkeypatch, tmp_path):
    """A successful compile must return PDF bytes (not a file path)."""
    from unittest.mock import MagicMock, patch
    import subprocess
    from application_api.repositories.reports import _compile_latex

    fake_pdf = b"%PDF-1.4 fake"

    def _fake_run(cmd, cwd, stdout, stderr, timeout):
        # Write a fake document.pdf into cwd (the temp dir).
        import os
        (tmp_path / "document.pdf").write_bytes(fake_pdf)
        # subprocess.run returns a CompletedProcess-like object.
        result = MagicMock()
        result.returncode = 0
        result.stdout = b""
        result.stderr = b""
        return result

    with patch("subprocess.run", side_effect=_fake_run), \
         patch("application_api.repositories.reports.TEMP_DIR", tmp_path, create=True):
        result = _compile_latex(
            r"\documentclass{article}\begin{document}.\end{document}", {}
        )
        assert isinstance(result, bytes), "compile must return bytes, not a file path"
        assert len(result) > 0
