"""The Population UI's vocabulary, translated to cohort rows."""



from __future__ import annotations

import pytest

from agent_system.services import population_review

pytestmark = pytest.mark.unit


def _cohort(*members, frozen=None, criteria=None):
    return {
        "cohort_id": "cohort-1",
        "frozen_at": frozen,
        "criteria": {"schema_version": 1, **(criteria or {})},
        "label_definition": {"schema_version": 1, "kind": "binary", "field": "has_target_disease"},
        "selection_summary": {"schema_version": 1, "anatomy_validation": {"anatomy_validated": True}},
        "members": list(members),
    }


def _member(uid, ordinal, value=None, source="pending", confidence=None, provenance=None):
    return {
        "member_id": f"member-{ordinal}",
        "volume_series_instance_uid": uid,
        "subject_key": f"patient-{ordinal}",
        "ordinal": ordinal,
        "label": None if value is None else {"has_target_disease": value},
        "label_source": source,
        "label_confidence": confidence,
        "label_provenance": provenance,
    }


def test_label_records_keep_the_uis_shape_and_ordinal_order():
    cohort = _cohort(
        _member("v2", 2, True, "llm_extraction", "high", {"source_evidence": "report says so"}),
        _member("v1", 1),
    )
    records = population_review.label_records(cohort)
    assert [record["patient_id"] for record in records] == ["v1", "v2"]
    assert records[0]["has_target_disease"] == ""
    assert records[0]["confidence"] == "unlabeled"
    assert records[0]["requires_manual_review"] is True
    assert records[1]["has_target_disease"] == "true"
    assert records[1]["confidence"] == "high"
    assert records[1]["source_evidence"] == "report says so"


def test_a_manual_override_is_reported_as_the_uis_confidence_string():
    cohort = _cohort(_member("v1", 1, False, "manual_override"))
    (record,) = population_review.label_records(cohort)
    assert record["confidence"] == "manual override"
    assert record["label_source"] == "manual_override"
    assert record["has_target_disease"] == "false"


def test_cohort_state_is_the_old_config_view_assembled_from_rows():
    cohort = _cohort(
        _member("v1", 1, True, "manual_override"),
        _member("v2", 2),
        criteria={"labelling_mode": "manual", "inclusion_criteria": ["adult"]},
    )
    state = population_review.cohort_state(cohort)
    assert state["datasetlist"] == ["v1", "v2"]
    assert state["labelling_mode"] == "manual"
    assert state["inclusion_criteria"] == ["adult"]
    assert state["cohort_approved"] is False
    assert state["anatomy_validated"] is True

    approved = population_review.cohort_state(_cohort(_member("v1", 1, True), frozen="2026-09-15T00:00:00Z"))
    assert approved["cohort_approved"] is True


def test_cohort_state_of_a_session_with_no_cohort_is_empty_not_an_error():
    state = population_review.cohort_state(None)
    assert state["datasetlist"] == [] and state["cohort_approved"] is False


def test_only_changed_labels_are_written_and_they_are_manual_overrides():
    cohort = _cohort(
        _member("v1", 1, True, "llm_extraction", "high"),
        _member("v2", 2),
    )
    writes = population_review.manual_label_writes(
        cohort,
        [
            # unchanged: an LLM label stays an LLM label
            {"patient_id": "v1", "has_target_disease": "true", "confidence": "high"},
            # the doctor labelled the unlabelled one
            {"patient_id": "v2", "has_target_disease": "false", "confidence": "manual override"},
            # a volume outside the cohort is ignored, not invented
            {"patient_id": "v9", "has_target_disease": "true", "confidence": "manual override"},
        ],
    )
    assert [write["volume_series_instance_uid"] for write in writes] == ["v2"]
    assert writes[0]["has_target_disease"] is False
    assert writes[0]["label_source"] == "manual_override"
    assert writes[0]["label_provenance"]["previous_source"] == "pending"


def test_clearing_a_label_is_recorded_as_a_manual_override_with_no_value():
    cohort = _cohort(_member("v1", 1, True, "llm_extraction", "high"))
    (write,) = population_review.manual_label_writes(
        cohort, [{"patient_id": "v1", "has_target_disease": "", "confidence": "manual override"}]
    )
    assert write["has_target_disease"] is None
    assert write["label_provenance"]["requires_manual_review"] is True


def test_an_uploaded_targets_file_labels_members_and_reports_the_rest():
    cohort = _cohort(_member("v1", 1), _member("v2", 2))
    csv_bytes = b"id,name,target\nv1,patient-1,1\nv2,patient-2,0\nv9,patient-9,1\n"
    writes, unmatched = population_review.uploaded_target_writes(cohort, csv_bytes)
    assert [(item["volume_series_instance_uid"], item["has_target_disease"]) for item in writes] == [
        ("v1", True),
        ("v2", False),
    ]
    assert all(item["label_source"] == "uploaded_targets" for item in writes)
    # Rows that name no cohort volume are reported, never silently dropped.
    assert unmatched == ["v9"]


def test_the_review_payload_and_snapshot_are_derived_from_the_pairs():
    batch = {
        "segmentation_run_id": "run-1",
        "review_status": "submitted",
        "config": {"correlation_run_id": "corr-1", "final_plan": {"targets": [], "selected_models": []}},
        "pairs": [
            {
                "pair_key": "v1__spleen",
                "volume_series_instance_uid": "v1",
                "target_key": "spleen",
                "status": "ready",
                "review_decision": "accepted",
                "quality": {"schema_version": 1, "work_state": "ready"},
            },
            {
                "pair_key": "v2__spleen",
                "volume_series_instance_uid": "v2",
                "target_key": "spleen",
                "status": "ready",
                "review_decision": "rejected",
                "quality": {"schema_version": 1, "work_state": "ready"},
            },
        ],
    }
    assert population_review.review_payload(batch) == {
        "accepted": ["v1__spleen"],
        "rejected": ["v2__spleen"],
        "modified": [],
    }
    manifest = population_review.snapshot_manifest(batch)
    assert manifest["stage"] == "reviewed"
    assert manifest["run_id"] == "corr-1"
    assert [item["pair_id"] for item in manifest["pairs"]] == ["v1__spleen", "v2__spleen"]
