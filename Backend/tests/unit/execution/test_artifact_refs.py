"""Artifact refs converted to the stored {table, ids} form and back."""

from __future__ import annotations

import uuid

import pytest

from agent_system.execution.artifact_refs import (
    ITEMS,
    StoredRefError,
    artifact_refs,
    pointer,
    stored_port,
)
from agent_system.toolkit.artifacts.builtin import (
    COHORT,
    FEATURE_IMPORTANCE,
    FEATURE_TABLE,
    MODELING_OUTPUT,
)
from agent_system.toolkit.ports import ExtractedArtifact

pytestmark = pytest.mark.unit


def _checked(artifact_type, ref, summary):
    return artifact_type.validate_artifact(ExtractedArtifact(ref=ref, summary=summary))


def test_one_artifact_is_stored_by_table_and_key():
    cohort_id = str(uuid.uuid4())
    stored = stored_port(COHORT, [_checked(COHORT, {"cohort_id": cohort_id}, {"size": 12, "frozen": True})])
    assert stored == {
        "type": "Cohort",
        "ref": {"table": "cohorts", "ids": [cohort_id]},
        "summary": {"size": 12, "label_counts": {}, "frozen": True},
        "phi": True,
    }
    assert pointer(COHORT, stored["ref"], many=False) == cohort_id
    assert artifact_refs(COHORT, stored["ref"], stored["summary"]) == [{"cohort_id": cohort_id}]


def test_a_list_pointer_stays_one_artifact():
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    artifact = _checked(MODELING_OUTPUT, {"model_result_ids": ids}, {"metric_name": "auc", "n_models": 2})
    stored = stored_port(MODELING_OUTPUT, [artifact])
    assert stored["ref"] == {"table": "model_results", "ids": ids}
    assert pointer(MODELING_OUTPUT, stored["ref"], many=False) == ids
    assert artifact_refs(MODELING_OUTPUT, stored["ref"], stored["summary"]) == [{"model_result_ids": ids}]


def test_a_many_port_keeps_each_summary():
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    artifacts = [_checked(FEATURE_TABLE, {"feature_set_id": key}, {"n_rows": n, "n_features": 9})
                 for key, n in ((first, 10), (second, 20))]
    stored = stored_port(FEATURE_TABLE, artifacts)
    assert stored["ref"]["ids"] == [first, second]
    assert [item["n_rows"] for item in stored["summary"][ITEMS]] == [10, 20]
    assert pointer(FEATURE_TABLE, stored["ref"], many=True) == [first, second]
    with pytest.raises(StoredRefError, match="one FeatureTable expected"):
        pointer(FEATURE_TABLE, stored["ref"], many=False)


def test_a_qualifier_travels_in_the_summary_and_comes_back():
    feature_set_id = str(uuid.uuid4())
    artifact = _checked(FEATURE_IMPORTANCE, {"feature_set_id": feature_set_id, "method": "rf"},
                        {"top_features": ["glcm_contrast"]})
    stored = stored_port(FEATURE_IMPORTANCE, [artifact])
    assert stored["ref"] == {"table": "feature_sets", "ids": [feature_set_id]}
    assert stored["summary"]["method"] == "rf"
    assert artifact_refs(FEATURE_IMPORTANCE, stored["ref"], stored["summary"]) == [
        {"feature_set_id": feature_set_id, "method": "rf"}
    ]


def test_a_ref_into_another_table_is_refused():
    with pytest.raises(StoredRefError, match="points at cohorts"):
        pointer(COHORT, {"table": "feature_sets", "ids": [str(uuid.uuid4())]}, many=False)
