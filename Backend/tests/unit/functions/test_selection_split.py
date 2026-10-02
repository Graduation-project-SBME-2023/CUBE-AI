"""Feature selection must not fit on rows it will later be scored against.

These tests pin the split *decision* only — no sklearn, no PyCaret, no fitted model —
so the guarantee is checked in the deterministic suite rather than behind a live run.
"""

from __future__ import annotations

import pytest

from agent_system.toolpacks.radiomics import selection_split as module


def _labels(n_pos: int, n_neg: int) -> list[int]:
    return [1] * n_pos + [0] * n_neg


@pytest.mark.unit
def test_holdout_is_excluded_from_training_and_covers_every_row():
    labels = _labels(10, 10)

    split = module.plan_selection_split(n_rows=20, labels=labels, holdout_size=0.2)

    assert split.has_holdout
    assert not split.label_supervised_on_full_data
    assert set(split.train_index).isdisjoint(split.holdout_index)
    assert sorted(split.train_index + split.holdout_index) == list(range(20))


@pytest.mark.unit
def test_split_is_deterministic_for_a_seed_and_moves_with_it():
    labels = _labels(10, 10)

    first = module.plan_selection_split(n_rows=20, labels=labels, seed=42)
    again = module.plan_selection_split(n_rows=20, labels=labels, seed=42)
    other = module.plan_selection_split(n_rows=20, labels=labels, seed=7)

    assert first.holdout_index == again.holdout_index
    assert first.holdout_index != other.holdout_index


@pytest.mark.unit
def test_a_subject_never_straddles_the_split():
    # 12 subjects, two rows each (e.g. two ROIs of one patient).
    groups = [f"P{i:02d}" for i in range(12) for _ in range(2)]
    labels = [i % 2 for i in range(12) for _ in range(2)]

    split = module.plan_selection_split(
        n_rows=24,
        labels=labels,
        group_values=groups,
        group_column="subject_id",
        holdout_size=0.25,
    )

    assert split.has_holdout
    held = {groups[i] for i in split.holdout_index}
    trained = {groups[i] for i in split.train_index}
    assert held.isdisjoint(trained), "a patient's rows appeared on both sides"
    assert split.strategy == "grouped_stratified"


@pytest.mark.unit
def test_training_always_retains_every_class():
    labels = _labels(14, 6)

    split = module.plan_selection_split(n_rows=20, labels=labels, holdout_size=0.3)

    assert split.has_holdout
    assert {labels[i] for i in split.train_index} == {0, 1}


@pytest.mark.unit
def test_tiny_cohort_degrades_honestly_instead_of_leaking_silently():
    # The shipped 14-subject cohort shape: too small to hold anything out.
    labels = _labels(4, 4)

    split = module.plan_selection_split(n_rows=8, labels=labels, holdout_size=0.2)

    assert not split.has_holdout
    assert split.label_supervised_on_full_data is True
    assert split.degraded_reason
    assert split.train_index == list(range(8))


@pytest.mark.unit
def test_single_member_class_degrades_rather_than_emptying_it_from_training():
    labels = _labels(1, 19)

    split = module.plan_selection_split(n_rows=20, labels=labels, holdout_size=0.2)

    assert not split.has_holdout
    assert split.label_supervised_on_full_data is True
    assert "class" in (split.degraded_reason or "")


@pytest.mark.unit
@pytest.mark.parametrize("holdout_size", [0.0, 1.0, -0.1])
def test_disabled_holdout_is_recorded_as_full_data_supervision(holdout_size):
    split = module.plan_selection_split(
        n_rows=40, labels=_labels(20, 20), holdout_size=holdout_size
    )

    assert not split.has_holdout
    assert split.label_supervised_on_full_data is True


@pytest.mark.unit
def test_regression_task_splits_without_stratifying():
    split = module.plan_selection_split(
        n_rows=30,
        labels=[float(i) for i in range(30)],
        task_type="regression",
        holdout_size=0.2,
    )

    assert split.has_holdout
    assert split.strategy == "random"
    assert len(split.holdout_index) == 6


@pytest.mark.unit
@pytest.mark.parametrize(
    ("columns", "expected"),
    [
        (["subject_id", "target"], "subject_id"),
        (["PatientID", "target"], "PatientID"),
        (["feature_a", "target"], None),
    ],
)
def test_group_column_detection(columns, expected):
    assert module.detect_group_column(columns) == expected


@pytest.mark.unit
def test_manifest_records_what_is_needed_to_replay_and_audit():
    split = module.plan_selection_split(
        n_rows=20, labels=_labels(10, 10), holdout_size=0.2, seed=99
    )

    manifest = split.to_manifest()

    assert manifest["seed"] == 99
    assert manifest["strategy"] == "stratified"
    assert manifest["label_supervised_on_full_data"] is False
    assert manifest["train_row_count"] + manifest["holdout_row_count"] == 20
