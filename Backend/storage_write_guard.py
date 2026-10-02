"""The runtime half of the current write cutover."""

from __future__ import annotations

import fnmatch
import os
import sys
import threading
from pathlib import Path, PurePosixPath

from storage_paths import HYPOTHESES_DIR

# (glob relative to workspace_<id>/, replacement storage, migration label)
RETIRED_WORKSPACE_PATHS: tuple[tuple[str, str, str], ...] = (
    # Step F — execution spine.
    ("state/scratchpad.json", "node_runs.scratchpad_notes/scratchpad_warnings/scratchpad_summary", "F"),
    # state.json duplicated what PlannerDbStateStore already reconstructs from
    # sessions.intent + session_graphs/graph_nodes/node_runs; nothing read most of what
    # it wrote (chat_history, knowledge_base, sources_guide,
    # knowledge_sections), and the one field a caller did read (hypothesis)
    # is available straight from the DB. Retired with no replacement file.
    ("state/state.json", "sessions.intent + session_graphs/graph_nodes/node_runs (PlannerDbStateStore)", "F"),
    ("outputs/agents/*_output.json", "node_runs.scratchpad_summary", "F"),
    # Step H — population and segmentation review.
    ("state/config.json", "cohorts.criteria/label_definition/selection_summary", "H"),
    ("data/populations/cohort_labels.csv", "cohort_members.label/label_source", "H"),
    ("data/populations/targets/*", "cohort_members (generated analysis rows)", "H"),
    ("data/populations/clinical_data.json", "cohort_members.clinical_data", "H"),
    ("data/populations/clinical_data.csv", "cohort_members.clinical_data", "H"),
    ("state/segmentation_manifest.json", "segmentation_batches/targets/pairs", "H"),
    ("temp/segmentations/segmentation_manifest.json.tmp", "segmentation_batches/targets/pairs", "H"),
    ("state/review_draft.json", "segmentation_pairs.review_decision", "H"),
    ("temp/segmentations/review_draft.json.tmp", "segmentation_pairs.review_decision", "H"),
    ("state/review_result.json", "segmentation_batches.review_status/reviewed_at", "H"),
    # Step I — feature extraction.
    ("outputs/features/radiomics_features.csv", "radiomics_features (generated export)", "I"),
    ("outputs/features/radiomic_features_target_*.csv", "radiomics_features", "I"),
    ("outputs/features/organ_feature_tables/*", "radiomics_features (generated export)", "I"),
    ("outputs/features/organ_availability_audit.csv", "radiomics_features + cohort_members", "I"),
    ("outputs/features/feature_schema.json", "feature_sets.feature_names", "I"),
    ("outputs/features/radiomics_params.yaml", "radiomics_features.configuration", "I"),
    ("outputs/features/top_*_features*.csv", "feature_sets.feature_names/feature_matrix", "I"),
    ("outputs/features/feature_selection_split.json", "feature_sets.selection", "I"),
    ("outputs/features/categorical_encodings.json", "feature_sets.extraction_config", "I"),
    # Label-derived importance is never feature_sets.analysis;
    # the ranking is the run's result, recorded with its output.
    ("outputs/features/feature_importance.csv", "node_runs.scratchpad_summary", "I"),
    ("outputs/features/plots/*", "stored_files (figure_png)", "I"),
    # Step J — modeling. The fitted pipelines under outputs/modeling/models/
    # are NOT retired: they are exception 2 and stay on disk.
    ("outputs/modeling/model_comparison_results.csv", "model_results.metrics", "J"),
    ("outputs/modeling/model_summary.csv", "model_results", "J"),
    ("outputs/modeling/models/*/*_results.csv", "model_results.metrics", "J"),
    ("outputs/modeling/models/*/holdout_predictions_*.csv", "stored_files via model_results.predictions_file_id", "J"),
    ("outputs/modeling/models/*/independent_eval_results_*.csv", "stored_files via model_results.predictions_file_id", "J"),
    ("outputs/modeling/models/*/plots/*", "stored_files (figure_png)", "J"),
    ("outputs/modeling/output/*", "stored_files + model_results (independent evaluation)", "J"),
    ("outputs/modeling/predictions*.csv", "stored_files (export_csv)", "J"),
    ("outputs/modeling/prediction_vs_actual.csv", "stored_files (export_csv)", "J"),
    ("outputs/modeling/residual_analysis.csv", "model_results.metrics", "J"),
    ("outputs/modeling/metrics_report.csv", "model_results.metrics", "J"),
    ("outputs/modeling/metrics.json", "model_results.metrics", "J"),
    ("outputs/modeling/*.log", "the disposable tool log under temp/", "J"),
    # Step K — reports and literature.
    # Physical paths (after workspace_subpath mapping):
    #   reports/           → outputs/reports/
    # Report source files: source_text lives in reports.source_text.
    # PDFs are never durable: compiled in temp, streamed, never persisted.
    ("outputs/reports/*.md", "reports.source_text (A19)", "K"),
    ("outputs/reports/*.tex", "reports.source_text (A19)", "K"),
    ("outputs/reports/*.pdf", "reports (no durable PDF — A19)", "K"),
    # literature_review_output/ and state/literature_context.json are fully
    # retired (storage_paths.workspace_subpath raises on either, so no
    # workspace path is ever produced for them to guard): the literature
    # agent returns its output in memory and the durable copy is written to
    # literature_reviews + literature_papers via the Application API (see
    # planner.py's literature review step), so
    # there is no retired outputs/literature/ path left to guard.
)

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
_installed = False
_install_lock = threading.Lock()


class RetiredWorkspaceWrite(PermissionError):
    """A write to a workspace file whose domain lives in PostgreSQL now."""


def _workspace_relative(path: object) -> str | None:
    if isinstance(path, int):
        return None
    try:
        text = os.fsdecode(os.fspath(path))
    except TypeError:
        return None
    root = str(HYPOTHESES_DIR)
    absolute = os.path.abspath(text)
    if not os.path.normcase(absolute).startswith(os.path.normcase(root)):
        return None
    try:
        relative = Path(absolute).relative_to(HYPOTHESES_DIR)
    except ValueError:
        return None
    parts = relative.parts
    if len(parts) < 2 or not parts[0].startswith("workspace_"):
        return None
    return str(PurePosixPath(*parts[1:]))


def retired_entry(path: object) -> tuple[str, str, str] | None:
    """The retired-path entry a filesystem path falls under, if any."""
    relative = _workspace_relative(path)
    if relative is None:
        return None
    for entry in RETIRED_WORKSPACE_PATHS:
        if fnmatch.fnmatchcase(relative, entry[0]):
            return entry
    return None


def check_write(path: object) -> None:
    entry = retired_entry(path)
    if entry is not None:
        pattern, replacement, step = entry
        raise RetiredWorkspaceWrite(
            f"Refusing to write {os.fspath(path)!r}: workspace file {pattern!r} was "
            f"retired by V1 build step {step} and its data lives in {replacement}. "
            "Write through the Application API; there is no file fallback (§8)."
        )


def _is_write(mode: object, flags: object) -> bool:
    if isinstance(mode, str) and any(ch in mode for ch in "wax+"):
        return True
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def _hook(event: str, args: tuple) -> None:
    if event == "open":
        path, mode, flags = (tuple(args) + (None, None, None))[:3]
        if _is_write(mode, flags):
            check_write(path)
    elif event in ("os.rename", "os.replace"):
        if len(args) >= 2:
            check_write(args[1])
    elif event == "shutil.copyfile":
        if len(args) >= 2:
            check_write(args[1])


def install_write_guard() -> None:
    """Install the audit hook once per process. Audit hooks cannot be removed,
    which is the point: nothing in the process can opt back into file writes."""
    global _installed
    with _install_lock:
        if _installed:
            return
        sys.addaudithook(_hook)
        _installed = True


def is_installed() -> bool:
    return _installed


__all__ = [
    "RETIRED_WORKSPACE_PATHS",
    "RetiredWorkspaceWrite",
    "check_write",
    "install_write_guard",
    "is_installed",
    "retired_entry",
]
