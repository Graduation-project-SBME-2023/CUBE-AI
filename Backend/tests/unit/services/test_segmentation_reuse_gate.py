"""The current step-B mask reuse gate (`is_reusable_mask_record`)."""

from __future__ import annotations

import pytest

from agent_system.agents.execution_agents.segmentation.resolution import (
    is_reusable_mask_record,
)

pytestmark = pytest.mark.unit


def _record(**overrides):
    base = {
        "label_series_instance_uid": "1.2.3.4",
        "origin": "assisted",
        "is_empty_mask": False,
        "archive_status": "available",
    }
    base.update(overrides)
    return base


def test_assisted_non_empty_mask_is_reusable():
    assert is_reusable_mask_record(_record()) is True


def test_pipeline_mask_is_not_reusable_without_a_review_decision():
    assert is_reusable_mask_record(_record(origin="pipeline")) is False


def test_imported_mask_is_never_reusable():
    """`imported` is the Orthanc sync's honest 'nobody declared how this was
    made'. Treating it as reusable is the bug step B closes."""
    assert is_reusable_mask_record(_record(origin="imported")) is False


def test_unclassified_legacy_row_is_not_reusable():
    assert is_reusable_mask_record(_record(origin=None)) is False
    assert is_reusable_mask_record(_record(origin="")) is False


def test_empty_mask_is_not_reusable():
    assert is_reusable_mask_record(_record(is_empty_mask=True)) is False
    assert is_reusable_mask_record(_record(is_empty_mask="true")) is False


def test_unknown_emptiness_is_not_reusable():
    """NULL means 'unknown', not 'not empty'. Nothing backfills False, because
    False is exactly what would put an unverified mask back into reuse."""
    assert is_reusable_mask_record(_record(is_empty_mask=None)) is False


def test_unavailable_archive_row_is_not_reusable():
    assert is_reusable_mask_record(_record(archive_status="unavailable")) is False
    assert is_reusable_mask_record(_record(archive_status="quarantined")) is False


def test_rejected_review_decision_blocks_reuse():
    assert (
        is_reusable_mask_record(_record(origin="assisted", review_decision="rejected"))
        is False
    )
    assert (
        is_reusable_mask_record(_record(origin="assisted", review_decision="accepted"))
        is True
    )


def test_row_without_an_identifier_is_not_reusable():
    assert is_reusable_mask_record(_record(label_series_instance_uid="")) is False
