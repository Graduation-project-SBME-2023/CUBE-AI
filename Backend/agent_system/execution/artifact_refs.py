"""Conversion between artifact refs and the refs stored on a run.

An output port's ``extract`` returns a ref shaped by its artifact type, such
as ``{"cohort_id": ...}`` or ``{"model_result_ids": [...]}``. A run stores
each output port as ``{type, ref: {table, ids}, summary, phi}``, a form the
Application API can check against the tables it allows and that a downstream
node can be pinned to.

- ``stored_port`` turns the artifacts of one output port into that stored form.
- ``pointer`` and ``artifact_refs`` turn a stored ref back into what a
  downstream tool argument or agent input expects.

Each ref model has exactly one pointer field, the key of a row (see
``POINTERS``). Any other field is a qualifier, such as
``FeatureImportance.method``. A stored ref keeps only the pointer, so
qualifiers are kept in the stored summary and read back from it.

The functions are pure and do no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..toolkit.artifacts.base import ArtifactType
from ..toolkit.ports import ExtractedArtifact

# Pointer field -> (table, whether the field holds a list of keys). The table
# names are those of the Application API; `node_runs` refers to the run itself.
POINTERS: Mapping[str, tuple[str, bool]] = {
    "cohort_id": ("cohorts", False),
    "segmentation_batch_id": ("segmentation_batches", False),
    "feature_set_id": ("feature_sets", False),
    "model_result_ids": ("model_results", True),
    "report_id": ("reports", False),
    "file_id": ("stored_files", False),
    "literature_review_id": ("literature_reviews", False),
    "run_id": ("node_runs", False),
    "series_uids": ("volume_series", True),
}
# The summary of a ``many`` port with several artifacts: one entry per artifact.
ITEMS = "items"


class StoredRefError(ValueError):
    """An artifact cannot be stored as ``{table, ids}``, or a stored ref read back."""


def _pointer_field(artifact_type: ArtifactType) -> str:
    found = [name for name in artifact_type.ref_model.model_fields if name in POINTERS]
    if len(found) != 1:
        raise StoredRefError(
            f"{artifact_type.name} refs need exactly one pointer field "
            f"({', '.join(sorted(POINTERS))}), found {found or 'none'}"
        )
    return found[0]


def _qualifiers(artifact_type: ArtifactType, pointer: str) -> list[str]:
    return [name for name in artifact_type.ref_model.model_fields if name != pointer]


def stored_port(
    artifact_type: ArtifactType, artifacts: Sequence[ExtractedArtifact], *, phi: bool | None = None
) -> dict[str, Any]:
    """Return the stored form ``{type, ref: {table, ids}, summary, phi}`` of one output port.

    ``artifacts`` must already be validated with
    ``ArtifactType.validate_artifact``. When a ``many`` port yields several
    artifacts, they must share the same qualifiers, and each artifact's
    summary is kept as one entry of ``summary["items"]``.
    """
    if not artifacts:
        raise StoredRefError(f"no {artifact_type.name} artifact to store")
    pointer = _pointer_field(artifact_type)
    table, is_list = POINTERS[pointer]
    qualifiers = _qualifiers(artifact_type, pointer)
    if is_list and len(artifacts) > 1:
        raise StoredRefError(f"{artifact_type.name} already holds a list; a port takes one of them")

    ids: list[str] = []
    for artifact in artifacts:
        value = artifact.ref[pointer]
        if is_list:
            ids.extend(str(item) for item in value)
        else:
            ids.append(str(value))
    kept = {name: artifacts[0].ref[name] for name in qualifiers}
    if any({name: a.ref[name] for name in qualifiers} != kept for a in artifacts[1:]):
        raise StoredRefError(f"{artifact_type.name} artifacts of one port must share {qualifiers}")

    if len(artifacts) == 1:
        summary = dict(artifacts[0].summary)
    else:
        summary = {ITEMS: [dict(a.summary) for a in artifacts]}
    clash = sorted(set(kept) & set(summary))
    if clash:
        raise StoredRefError(f"{artifact_type.name} summary already uses {clash}")
    return {
        "type": artifact_type.name,
        "ref": {"table": table, "ids": ids},
        "summary": summary | kept,
        "phi": artifact_type.phi_default if phi is None else phi,
    }


def _ids(artifact_type: ArtifactType, stored_ref: Mapping[str, Any]) -> tuple[str, bool, list[str]]:
    pointer = _pointer_field(artifact_type)
    table, is_list = POINTERS[pointer]
    if stored_ref.get("table") != table or not stored_ref.get("ids"):
        raise StoredRefError(f"a {artifact_type.name} ref points at {table}, not {dict(stored_ref)}")
    return pointer, is_list, [str(item) for item in stored_ref["ids"]]


def pointer(artifact_type: ArtifactType, stored_ref: Mapping[str, Any], *, many: bool) -> Any:
    """Return the value passed to a tool's input ``param``.

    This is the key itself, or a list of keys for a ``many`` port or a
    list-valued pointer such as ``model_result_ids``.
    """
    _, is_list, ids = _ids(artifact_type, stored_ref)
    if many or is_list:
        return ids
    if len(ids) != 1:
        raise StoredRefError(f"one {artifact_type.name} expected, the ref holds {len(ids)}")
    return ids[0]


def artifact_refs(
    artifact_type: ArtifactType, stored_ref: Mapping[str, Any], summary: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Rebuild the artifact refs behind a stored ref, in the shape of ``artifact_type``.

    Qualifiers are read from ``summary``. The result is what an agent is shown
    for one of its inputs.
    """
    pointer_name, is_list, ids = _ids(artifact_type, stored_ref)
    qualifiers = {name: summary.get(name) for name in _qualifiers(artifact_type, pointer_name)}
    if is_list:
        return [{pointer_name: ids, **qualifiers}]
    return [{pointer_name: item, **qualifiers} for item in ids]
