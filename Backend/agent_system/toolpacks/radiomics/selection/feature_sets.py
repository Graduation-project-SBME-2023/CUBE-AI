"""The frozen feature sets modeling trains on: one per requested top-N, plus
every feature (``all_features``). Each carries the selection manifest;
train/holdout are reconstructed from its ``holdout_groups`` at export time and
never stored as CSVs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from ...common import domain_api
from .. import digests as provenance
from ..feature_set_builder import FEATURE_SET_SCHEMA_VERSION, VOLUME_KEY, matrix_from_frame, quality_summary
from .charts import label_free_qc
from .matrix import SelectionMatrix
from .ranking import top_features

ALL_FEATURES = "all_features"


@dataclass(frozen=True)
class CandidateSet:
    set_key: str
    top_n: int | None
    names: list[str]


def candidate_sets(
    method: str, matrix: SelectionMatrix, ranking: pd.DataFrame, top_ns: list[int], n_jobs: int = 1
) -> list[CandidateSet]:
    """The sets to freeze. A requested N larger than the feature count is
    clamped, and two requests that clamp to the same N give one set. RFE refits
    for every N, so this can be slow: run it off the event loop."""
    width = matrix.X.shape[1]
    sizes = list(dict.fromkeys(min(n, width) for n in top_ns))
    sets = [
        CandidateSet(f"top_{n}", n, top_features(method, matrix, ranking, n, n_jobs)) for n in sizes
    ]
    return [*sets, CandidateSet(ALL_FEATURES, None, [str(name) for name in matrix.X.columns])]


def _payload(
    candidate: CandidateSet,
    matrix: SelectionMatrix,
    *,
    cohort_id: str,
    base_config: dict[str, Any],
    selection: dict[str, Any],
    source_feature_ids: list[str] | None,
    source_digest: str | None,
    stability: list[dict[str, Any]],
    notes: list[str],
) -> dict[str, Any]:
    # Keyed by volume, not subject_key: matrix_from_frame() rejects a repeated
    # key, and a patient may contribute several labelled volumes. A measured
    # column keeps its measured values (a missing value stays missing, with
    # its reason recorded); encoded columns exist only in the matrix.
    frame = pd.DataFrame({VOLUME_KEY: matrix.volume_keys})
    for name in candidate.names:
        frame[name] = matrix.values_of(name)
    values, missing = matrix_from_frame(frame, candidate.names)
    config = {**base_config, "top_n": candidate.top_n}
    return {
        "cohort_id": cohort_id,
        "feature_names": candidate.names,
        "feature_matrix": values,
        "source_feature_ids": source_feature_ids,
        "selection": selection,
        "extraction_config": config,
        "analysis": label_free_qc(matrix.X, candidate.names, stability),
        "quality_summary": quality_summary(
            subjects=len(values), feature_count=len(candidate.names), missing_values=missing, notes=notes
        ),
        "config_digest": provenance.document_digest(config),
        "source_digest": source_digest,
        "schema_version": FEATURE_SET_SCHEMA_VERSION,
    }


async def freeze(
    candidates: list[CandidateSet],
    matrix: SelectionMatrix,
    **payload_fields: Any,
) -> list[dict[str, Any]]:
    """Create and freeze each set; the frozen sets, as the tool reports them."""
    frozen_sets = []
    for candidate in candidates:
        created = await domain_api.create_feature_set(
            candidate.set_key, _payload(candidate, matrix, **payload_fields)
        )
        frozen = await domain_api.freeze_feature_set(str(created["feature_set_id"]))
        frozen_sets.append({
            "set_key": candidate.set_key,
            "feature_set_id": str(frozen["feature_set_id"]),
            "feature_count": len(candidate.names),
            "frozen_at": frozen.get("frozen_at"),
            "label_supervised_on_full_data": payload_fields["selection"]["label_supervised_on_full_data"],
        })
    return frozen_sets


__all__ = ["ALL_FEATURES", "CandidateSet", "candidate_sets", "freeze"]
