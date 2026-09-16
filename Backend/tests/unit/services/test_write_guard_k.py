"""Unit tests for Phase K write-guard additions."""

from __future__ import annotations

import pytest

from storage_paths import ws_sub
from storage_write_guard import RetiredWorkspaceWrite, check_write, retired_entry

pytestmark = pytest.mark.unit

# We reuse the same guard-session workspace id as the existing write-guard tests.
_SESSION = "guard-session"


def _path(logical: str):
    """Resolve a logical workspace path to its physical absolute path."""
    return ws_sub(_SESSION, logical)


# ── K retired paths that must raise ──────────────────────────────────────────

@pytest.mark.parametrize(
    "logical",
    [
        # Report source files — logical "reports/" → physical "outputs/reports/"
        "reports/manuscript.tex",
        "reports/draft.md",
        "reports/final_report.pdf",
    ],
)
def test_k_retired_paths_raise(logical):
    path = _path(logical)
    entry = retired_entry(path)
    assert entry is not None, (
        f"Expected logical {logical!r} (physical {path}) to be retired by K"
    )
    assert entry[2] == "K", f"Expected step K, got {entry[2]!r}"
    with pytest.raises(RetiredWorkspaceWrite):
        check_write(path)


# ── J retired paths still raise (regression) ─────────────────────────────────

@pytest.mark.parametrize(
    "logical",
    [
        # "modeling/" is the logical name that maps to physical "outputs/modeling/"
        "modeling/model_comparison_results.csv",
        "modeling/model_summary.csv",
        "modeling/metrics.json",
    ],
)
def test_j_retired_paths_still_raise(logical):
    path = _path(logical)
    entry = retired_entry(path)
    assert entry is not None, (
        f"Expected logical {logical!r} (physical {path}) to still be retired by J"
    )
    assert entry[2] == "J"


# ── Non-retired paths that must pass ─────────────────────────────────────────

@pytest.mark.parametrize(
    "logical",
    [
        # exception 2: model binaries are NOT retired
        "modeling/models/svc/svc.pkl",
        "modeling/models/rf/random_forest.pkl",
        # temp segmentations are not retired
        "temp_segmentations/something.tmp",
        # A CSV named with "reports" in the name but not under reports/ is fine
        "outputs/modeling/reports_summary.csv",
        # Literature output — logical "literature_review_output/" now maps to
        # physical "temp/literature/" (disposable scratch); the durable copy
        # lives in literature_reviews + literature_papers via the API.
        "literature_review_output/context.json",
        "literature_review_output/papers_page1.json",
        "literature_review_output/review_summary.json",
        "literature_review_output/other_file.json",
    ],
)
def test_non_retired_paths_pass(logical):
    path = _path(logical)
    # For model binaries specifically, verify no guard entry
    if "models/" in logical and logical.endswith(".pkl"):
        entry = retired_entry(path)
        assert entry is None, (
            f"{logical!r} should not be retired (§1 exception 2 — model binaries stay on disk)"
        )
    # For all non-retired paths, check_write must not raise
    check_write(path)


# ── Paths outside the workspace root are never blocked ───────────────────────

def test_path_outside_workspace_root_is_never_blocked(tmp_path):
    """A path completely outside the hypotheses root is never retired."""
    check_write(tmp_path / "outputs" / "reports" / "report.pdf")
    check_write(tmp_path / "outputs" / "literature" / "context.json")
