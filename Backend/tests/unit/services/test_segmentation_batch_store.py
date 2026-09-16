"""The segmentation checkpoint, once it stopped being a file."""

from __future__ import annotations

import pytest

from agent_system.agents.execution_agents.segmentation import batch_store

pytestmark = pytest.mark.unit


def _item(**overrides):
    item = {
        "pair_id": "volume-1__spleen",
        "volume_uid": "volume-1",
        "target_id": "spleen",
        "artifact_key": "key-1",
        "sequence_index": 0,
        "status": "inferencing",
        "inference_job_id": "job-1",
        "source_segmentation_uid": "seg-1",
        "reused": False,
        "is_empty_mask": False,
        "review_status": "pending",
        "mask_path": None,
    }
    item.update(overrides)
    return item


def test_fine_grained_work_states_map_onto_the_four_the_schema_allows():
    assert batch_store.pair_status(_item(status="pending")) == "pending"
    for running in ("reusing", "preparing", "inferencing", "inference_succeeded", "resolved"):
        assert batch_store.pair_status(_item(status=running)) == "running"
    assert batch_store.pair_status(_item(status="ready")) == "ready"
    assert batch_store.pair_status(_item(status="failed")) == "failed"
    # A rejected pair is a ready result with a negative decision, not a status.
    assert batch_store.pair_status(_item(status="rejected")) == "ready"


def test_the_detailed_state_is_kept_in_quality_and_identity_is_not_duplicated():
    payload = batch_store.pair_payload(_item(status="preparing", geometry_adjusted=True))
    assert payload["pair_key"] == "volume-1__spleen"
    assert payload["status"] == "running"
    assert payload["quality"]["work_state"] == "preparing"
    assert payload["quality"]["geometry_adjusted"] is True
    # Identity lives in the row's own columns.
    for duplicated in ("pair_id", "volume_uid", "target_id", "review_status", "inference_job_id"):
        assert duplicated not in payload["quality"]


def test_a_candidate_seg_is_only_named_once_the_archive_row_exists():
    """The pair's composite FK requires a real mask of the pair's own volume,
    and inference records the UID before the sync has created that row."""
    unconfirmed = batch_store.pair_payload(_item())
    assert unconfirmed["candidate_segmentation_uid"] is None
    confirmed = batch_store.pair_payload(_item(candidate_confirmed=True))
    assert confirmed["candidate_segmentation_uid"] == "seg-1"


def test_work_items_round_trip_through_the_stored_pairs():
    item = _item(status="ready", candidate_confirmed=True, gif_path="overlays/key-1.gif")
    payload = batch_store.pair_payload(item)
    batch = {
        "review_status": "pending",
        "pairs": [
            {
                "pair_key": payload["pair_key"],
                "volume_series_instance_uid": "volume-1",
                "target_key": "spleen",
                "status": payload["status"],
                "external_job_id": payload["external_job_id"],
                "candidate_segmentation_uid": payload["candidate_segmentation_uid"],
                "selected_segmentation_uid": None,
                "review_decision": None,
                "quality": payload["quality"],
            }
        ],
    }
    (restored,) = batch_store.work_items_from_batch(batch)
    assert restored["pair_id"] == item["pair_id"]
    assert restored["volume_uid"] == "volume-1"
    assert restored["target_id"] == "spleen"
    assert restored["status"] == "ready"
    assert restored["gif_path"] == "overlays/key-1.gif"
    assert restored["inference_job_id"] == "job-1"
    assert restored["source_segmentation_uid"] == "seg-1"
    assert restored["candidate_confirmed"] is True
    assert restored["review_status"] == "pending"


def test_a_submitted_rejection_comes_back_as_a_rejected_work_item():
    batch = {
        "review_status": "submitted",
        "pairs": [
            {
                "pair_key": "volume-1__spleen",
                "volume_series_instance_uid": "volume-1",
                "target_key": "spleen",
                "status": "ready",
                "review_decision": "rejected",
                "quality": {"schema_version": 1, "work_state": "ready"},
            }
        ],
    }
    (restored,) = batch_store.work_items_from_batch(batch)
    assert restored["status"] == "rejected"
    assert restored["review_status"] == "rejected"


def test_a_batch_cannot_be_recorded_without_the_run_that_produced_it():
    """/: identity is the run. There is no manifest to fall back to."""
    with pytest.raises(RuntimeError, match="agent_run_id"):
        batch_store.SegmentationBatchStore(None)


def test_the_stage_the_module_reports_maps_onto_the_review_status():
    assert batch_store._STAGE_REVIEW_STATUS["processing"] == "not_ready"
    assert batch_store._STAGE_REVIEW_STATUS["awaiting_review"] == "pending"
    # "reviewed" is not something the module may set: only the review endpoint
    # moves a batch to submitted.
    assert "reviewed" not in batch_store._STAGE_REVIEW_STATUS
