"""``radiomics_analyze_importance``: rank this run's radiomics features and
freeze the feature sets modeling trains on.

1. the input: the run's recorded radiomics rows (or a generic CSV), labelled
   from the approved cohort (``selection.matrix``);
2. the matrix and the leakage-safe selection split — every step that looks at
   labels fits on the training rows only;
3. the ranking and the alternative rankings (``selection.ranking``);
4. the chart data and the archival figures (``selection.charts``, ``.plots``);
5. one frozen set per requested top-N, plus every feature (``selection.feature_sets``).

The computing steps run off the event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from .._toolkit import error_result, tool
from ..common import domain_api, settings
from ..common.emit import emit, emit_progress
from ..common.manifests import INTERIM_HEAVY
from ..common.values import int_list, json_safe
from ..common.workspace import WorkspacePath
from .feature_set_builder import FEATURE_SET_SCHEMA_VERSION, SUBJECT_KEY, FeatureSetBuildError, selection_document
from .selection import charts, feature_sets, plots, ranking
from .selection.matrix import ENCODINGS, SelectionInputError, build_matrix, source_from_csv, source_from_rows
from storage_paths import TEMP_DIR

logger = logging.getLogger(__name__)

TOTAL_STAGES = 5
TOOL_TIMEOUT_S = 1800
DEFAULT_TOP_FEATURES = 10
TOP_FEATURES_REPORTED = 10


class AnalyzeImportanceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="ignore")

    input_path: WorkspacePath = Field(
        default=None,
        description=(
            "A generic CSV to select from. Omit it — the normal case — and the tool "
            "selects from the radiomics rows this run recorded."
        ),
    )
    top_features: Union[int, str, List[Union[int, str]]] = Field(
        default="10", description="The sizes of the frozen top-N sets, e.g. '10' or '5,10,20'."
    )
    task_type: Optional[str] = Field(
        default=None, description="'classification' or 'regression'; detected from the target when omitted."
    )
    method: str = Field(default="rf", description="'rf', 'f_test', 'mutual_info' or 'rfe'.")
    encode_categorical: str = Field(default="auto", description="'auto', 'onehot', 'label', 'target' or 'none'.")
    max_onehot_cardinality: int = Field(default=10)
    create_plots: bool = Field(default=True)
    top_n_plot: int = Field(default=30)
    holdout_size: float = Field(
        default=0.2,
        description=(
            "Fraction of subjects excluded from feature selection so their labels do "
            "not leak into the selected set. Set to 0 to disable, which makes the "
            "selection label-supervised on every row and is recorded as such."
        ),
    )
    split_seed: int = Field(default=42, description="Seed for the selection holdout split.")
    group_column: Optional[str] = Field(
        default=None,
        description=(
            "Ignored since V1 step I: the split is always grouped by subject_key, the "
            "cohort's patient-level split group, so a patient's rows never straddle it."
        ),
    )


async def _source(params: AnalyzeImportanceInput, run_id: str, analysis_rows: list[dict[str, Any]]):
    if params.input_path:
        return await asyncio.to_thread(source_from_csv, params.input_path, analysis_rows)
    rows = await domain_api.run_radiomics_rows(run_id)
    if not rows:
        return None
    return await asyncio.to_thread(source_from_rows, rows, analysis_rows)


async def _publish_figures(paths: list[str], run_id: str, method: str) -> tuple[list[dict], list[str]]:
    """The figures as ``figure_png`` artifacts. A replay that rendered
    different bytes under the same key keeps the earlier figure, and says so."""
    figures: list[dict[str, Any]] = []
    warnings: list[str] = []
    for path in sorted(paths):
        name = os.path.basename(path)
        payload = await asyncio.to_thread(_read, path)
        if not payload:
            continue
        try:
            artifact = await domain_api.publish_artifact(
                logical_key=f"features/{run_id}/{name}",
                kind="figure_png",
                payload=payload,
                filename=name,
                mime_type="image/png",
                metadata={"schema_version": 1, "producer": "radiomics_analyze_importance", "method": method},
            )
        except domain_api.DomainApiError as exc:
            if exc.status_code != 409:
                raise
            warnings.append(f"{name}: kept the earlier published figure ({exc})")
            continue
        figures.append({
            "artifact_id": str(artifact["file_id"]),
            "logical_key": artifact["logical_key"],
            "display_filename": artifact.get("display_filename"),
        })
    return figures, warnings


def _read(path: str) -> bytes:
    with open(path, "rb") as stream:
        return stream.read()


async def _analyze(params: AnalyzeImportanceInput) -> str | dict[str, Any]:
    method = params.method if params.method in ranking.METHODS else ranking.DEFAULT_METHOD
    if method != params.method:
        logger.warning("Unsupported method %r; using %r", params.method, method)
    encode = params.encode_categorical if params.encode_categorical in ENCODINGS else "auto"
    try:
        top_ns = sorted({n for n in (int_list(params.top_features) or [DEFAULT_TOP_FEATURES]) if n > 0})
    except ValueError as exc:
        return error_result("invalid_args", str(exc))
    if not top_ns:
        return error_result("invalid_args", "top_features must contain at least one positive integer.")
    if params.group_column and params.group_column != SUBJECT_KEY:
        logger.warning("group_column=%r ignored: the selection split is grouped by subject_key.", params.group_column)

    source_label = os.path.basename(params.input_path) if params.input_path else "this run's radiomics rows"
    emit("tool_log", f"Analysing feature importance for {source_label}...")
    run_id = domain_api.require_run()
    cohort = await domain_api.require_population_cohort()
    if not cohort.get("frozen_at"):
        return error_result(
            "cohort_not_approved",
            "The population cohort has not been approved; feature selection needs its frozen labels.",
        )
    analysis_rows = await domain_api.cohort_analysis_rows(cohort["cohort_id"])
    source = await _source(params, run_id, analysis_rows)
    if source is None:
        return error_result(
            "no_radiomics_rows",
            "This run has recorded no radiomics rows; call radiomics_extract_features first.",
        )

    n_jobs = settings.compute_cpu_threads()
    matrix = await asyncio.to_thread(
        build_matrix,
        source.frame,
        task_type=params.task_type,
        encode=encode,
        max_onehot_cardinality=params.max_onehot_cardinality,
        holdout_size=params.holdout_size,
        split_seed=params.split_seed,
    )
    emit_progress(current=1, total=TOTAL_STAGES, label="Loading & preprocessing data")

    ranked = await asyncio.to_thread(ranking.importance, method, matrix, n_jobs)
    alternative_rankings, stability = await asyncio.to_thread(
        ranking.alternatives, matrix, params.top_n_plot, n_jobs
    )
    emit_progress(current=2, total=TOTAL_STAGES, label=f"Computing importance ({method}, {matrix.task_type})")

    chart_data = await asyncio.to_thread(
        charts.chart_data, matrix, ranked,
        method=method, top_n_plot=params.top_n_plot, scatter_features=top_ns[-1], n_jobs=n_jobs,
    )
    emit_progress(current=3, total=TOTAL_STAGES, label="Building chart data")

    figure_paths: list[str] = []
    if params.create_plots:
        plots_dir = str(TEMP_DIR / "feature-selection" / run_id / "plots")
        figure_paths = await asyncio.to_thread(
            plots.draw_all, matrix.X, ranked, plots_dir, top_n=params.top_n_plot, method=method
        )
    figures, warnings = await _publish_figures(figure_paths, run_id, method)
    emit_progress(current=4, total=TOTAL_STAGES, label="Saving archival plot images")

    split_manifest = matrix.split.to_manifest()
    selection = selection_document(split_manifest)
    candidates = await asyncio.to_thread(feature_sets.candidate_sets, method, matrix, ranked, top_ns, n_jobs)
    frozen_sets = await feature_sets.freeze(
        candidates,
        matrix,
        cohort_id=str(cohort["cohort_id"]),
        base_config={
            "schema_version": FEATURE_SET_SCHEMA_VERSION,
            "source": source.kind,
            "radiomics_config_digests": source.config_digests,
            "selection_method": method,
            "task_type": matrix.task_type,
            "encode_categorical": encode,
            "max_onehot_cardinality": params.max_onehot_cardinality,
            "categorical_encodings": json_safe(matrix.encodings),
            "holdout_size": params.holdout_size,
            "split_seed": params.split_seed,
            "group_column": SUBJECT_KEY,
        },
        selection=selection,
        source_feature_ids=source.source_feature_ids,
        source_digest=source.source_digest,
        stability=stability,
        notes=source.notes,
    )
    emit_progress(current=5, total=TOTAL_STAGES, label="Freezing feature sets")
    emit("tool_log", "Feature Importance Analysis...Done")

    return json.dumps({
        "status": "success",
        "agent_run_id": run_id,  # tool result key, kept (plan.md: no success result shape changes)
        "task_type": matrix.task_type,
        "method": method,
        "feature_count": int(matrix.X.shape[1]),
        "source": source.kind,
        # The frozen sets modeling reads by id.
        "feature_sets": frozen_sets,
        "exports": {
            f"feature_set_{item['set_key']}": (
                f"/feature-sets/{item['feature_set_id']}/export?format=csv&include_subject_key=true"
            )
            for item in frozen_sets
        },
        "figures": figures,
        "top_features": ranked["feature"].head(TOP_FEATURES_REPORTED).tolist(),
        "warnings": warnings,
        # Whether the selected feature set is blind to the holdout labels. When
        # True, every downstream holdout metric is optimistic by an unknown
        # amount and the report must say so rather than imply a clean estimate.
        "label_supervised_on_full_data": matrix.split.label_supervised_on_full_data,
        "selection_split": split_manifest,
        "chart_data": chart_data,
        # Present only when at least one could be computed.
        **(
            {"alternative_rankings": json_safe(alternative_rankings), "stability_scores": json_safe(stability)}
            if alternative_rankings or stability
            else {}
        ),
    }, indent=2)


@tool(
    name="radiomics_analyze_importance",
    visibility={"agent"},
    timeout_s=TOOL_TIMEOUT_S,
    resources={"cpu_heavy"},
    data_scopes={"phi_read", "db_write"},
    **INTERIM_HEAVY,
)
async def radiomics_analyze_importance(params: AnalyzeImportanceInput) -> str | dict[str, Any]:
    """Perform feature importance analysis. Returns chart_data for frontend rendering."""
    try:
        return await _analyze(params)
    except (SelectionInputError, FeatureSetBuildError) as exc:
        return error_result("invalid_input", str(exc))
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc, "Feature selection could not complete")
