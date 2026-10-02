"""Feature-set assembly."""

from __future__ import annotations

import math

import pytest

pd = pytest.importorskip("pandas")
from agent_system.toolpacks.radiomics import feature_set_builder as builder
from agent_system.toolpacks.radiomics import selection_split as split_module

pytestmark = pytest.mark.unit


def _analysis(*members):
    """(volume, subject, target, ordinal)"""
    return [
        {"volume_series_instance_uid": v, "subject_key": s, "target": t, "ordinal": o}
        for v, s, t, o in members
    ]


def _row(row_id, volume, features, *, target="spleen", segment=1, digest="d"):
    return {
        "radiomics_feature_id": row_id,
        "source_volume_uid": volume,
        "segment_number": segment,
        "target_key": target,
        "features": features,
        "source_digest": digest * 64,
    }


# ----------------------------------------------------------------------
# From radiomics rows
# ----------------------------------------------------------------------
def test_rows_become_one_labelled_row_per_volume_in_ordinal_order():
    analysis = _analysis(("v2", "p2", 0, 2), ("v1", "p1", 1, 1), ("v3", "p3", 1, 3))
    rows = [
        _row("r3", "v3", {"a": 3.0, "b": 30.0}),
        _row("r1", "v1", {"a": 1.0, "b": 10.0}),
        _row("r2", "v2", {"a": 2.0, "b": float("nan")}),
    ]
    table = builder.wide_table_from_rows(rows, analysis)
    frame = table.frame
    # Rule 2: order comes from the cohort's ordinal, not from row arrival.
    assert frame[builder.VOLUME_KEY].tolist() == ["v1", "v2", "v3"]
    assert frame[builder.SUBJECT_KEY].tolist() == ["p1", "p2", "p3"]
    # The label is the cohort member's, joined for fitting only.
    assert frame[builder.TARGET_COLUMN].tolist() == [1, 0, 1]
    assert table.feature_columns == ["spleen__a", "spleen__b", "spleen_present"]
    assert sorted(table.source_feature_ids) == ["r1", "r2", "r3"]
    assert frame.loc[frame.volume_series_instance_uid == "v2", "spleen__b"].isna().all()


def test_a_labelled_volume_without_a_mask_keeps_missing_values():
    analysis = _analysis(("v1", "p1", 1, 1), ("v2", "p2", 0, 2))
    table = builder.wide_table_from_rows([_row("r1", "v1", {"a": 1.0})], analysis)
    record = table.frame.set_index(builder.VOLUME_KEY).loc["v2"]
    assert record["spleen_present"] == 0
    assert record["spleen__a"] is None or math.isnan(record["spleen__a"])


def test_unlabelled_volumes_are_not_features_of_the_set():
    analysis = _analysis(("v1", "p1", 1, 1))
    rows = [_row("r1", "v1", {"a": 1.0}), _row("r9", "v9", {"a": 9.0})]
    table = builder.wide_table_from_rows(rows, analysis)
    assert table.source_feature_ids == ["r1"]


def test_two_volumes_of_one_subject_each_get_their_own_row():
    """The label is per volume, not per patient: a subject with two volumes
    (e.g. one lesion per kidney) gets two rows, not a rejection."""
    analysis = _analysis(("v1", "p1", 1, 1), ("v2", "p1", 1, 2))
    rows = [_row("r1", "v1", {"a": 1.0}), _row("r2", "v2", {"a": 2.0})]
    table = builder.wide_table_from_rows(rows, analysis)
    frame = table.frame
    assert frame[builder.VOLUME_KEY].tolist() == ["v1", "v2"]
    assert frame[builder.SUBJECT_KEY].tolist() == ["p1", "p1"]
    assert frame[builder.TARGET_COLUMN].tolist() == [1, 1]


def test_volumes_of_one_subject_may_disagree_on_the_label():
    """A subject's volumes are independent observations: a benign lesion in
    one kidney and a malignant one in the other are two different, correct
    labels, not a conflict to reject."""
    analysis = _analysis(("v1", "p1", 1, 1), ("v2", "p1", 0, 2))
    table = builder.wide_table_from_rows([_row("r1", "v1", {"a": 1.0})], analysis)
    frame = table.frame
    assert frame[builder.SUBJECT_KEY].tolist() == ["p1", "p1"]
    assert frame[builder.TARGET_COLUMN].tolist() == [1, 0]


def test_a_multi_segment_target_is_named_per_segment():
    analysis = _analysis(("v1", "p1", 1, 1))
    rows = [
        _row("r1", "v1", {"a": 1.0}, segment=1),
        _row("r2", "v1", {"a": 2.0}, segment=2, digest="e"),
    ]
    table = builder.wide_table_from_rows(rows, analysis)
    assert {"spleen_label_1__a", "spleen_label_2__a"} <= set(table.feature_columns)


# ----------------------------------------------------------------------
# The matrix
# ----------------------------------------------------------------------
def test_the_matrix_is_rectangular_and_counts_missing_values():
    frame = pd.DataFrame(
        {builder.VOLUME_KEY: ["v1", "v2"], "a": [1.0, float("nan")], "b": [2.0, 3.0]}
    )
    matrix, missing = builder.matrix_from_frame(frame, ["b", "a"])
    assert matrix == {"v1": [2.0, 1.0], "v2": [3.0, None]}
    assert missing == 1
    assert all(len(values) == 2 for values in matrix.values())


@pytest.mark.parametrize(
    "name", ["target", "Target", "label", "subject_key", "volume_series_instance_uid", "ordinal"]
)
def test_identity_and_label_columns_never_enter_the_matrix(name):
    frame = pd.DataFrame({builder.VOLUME_KEY: ["v1"], name: [1.0]})
    with pytest.raises(builder.FeatureSetBuildError, match="cannot be a feature"):
        builder.matrix_from_frame(frame, [name])


def test_non_numeric_cells_are_rejected():
    frame = pd.DataFrame({builder.VOLUME_KEY: ["v1"], "a": ["high"]})
    with pytest.raises(builder.FeatureSetBuildError, match="not numeric"):
        builder.matrix_from_frame(frame, ["a"])


def test_reordering_json_keys_does_not_change_the_assembled_matrix():
    """Rule 2, reader side: rows come from the ordinal order, never key order."""
    matrix = {"p2": [2.0], "p1": [1.0], "p3": [3.0]}
    reordered = dict(reversed(list(matrix.items())))
    order = ["p1", "p2", "p3"]
    assert builder.assemble_rows(["a"], matrix, order) == [[1.0], [2.0], [3.0]]
    assert builder.assemble_rows(["a"], reordered, order) == builder.assemble_rows(
        ["a"], matrix, order
    )


def test_a_ragged_row_is_refused_at_assembly():
    with pytest.raises(builder.FeatureSetBuildError, match="not rectangular"):
        builder.assemble_rows(["a", "b"], {"p1": [1.0]}, ["p1"])


def test_missing_values_carry_a_reason():
    assert "reason" not in builder.quality_summary(subjects=2, feature_count=3, missing_values=0)[
        "missing_values"
    ]
    summary = builder.quality_summary(subjects=2, feature_count=3, missing_values=4)
    assert summary["missing_values"] == {"count": 4, "reason": builder.MISSING_VALUE_REASON}
    assert summary["schema_version"] == 1


# ----------------------------------------------------------------------
# Selection manifest
# ----------------------------------------------------------------------
def test_the_selection_document_is_exactly_the_manifest():
    split = split_module.plan_selection_split(
        n_rows=24,
        labels=[index % 2 for index in range(24)],
        group_values=[f"p{index // 2}" for index in range(24)],
        group_column=builder.SUBJECT_KEY,
        seed=7,
    )
    document = builder.selection_document(split.to_manifest())
    assert set(document) == {"schema_version", *builder.SELECTION_FIELDS}
    # Positional indices are meaningless once rows move; groups are durable.
    assert "train_index" not in document and "holdout_index" not in document
    assert document["holdout_groups"] == sorted(document["holdout_groups"])
    assert isinstance(document["label_supervised_on_full_data"], bool)


def test_no_subject_is_on_both_sides_and_the_seed_replays():
    """Grouped by subject_key, the same seed is the same split."""
    groups = [f"p{index // 2}" for index in range(30)]
    labels = [index // 2 % 2 for index in range(30)]
    first = split_module.plan_selection_split(
        n_rows=30, labels=labels, group_values=groups, group_column="subject_key", seed=11
    )
    again = split_module.plan_selection_split(
        n_rows=30, labels=labels, group_values=groups, group_column="subject_key", seed=11
    )
    assert first.holdout_groups == again.holdout_groups
    held = {groups[index] for index in first.holdout_index}
    trained = {groups[index] for index in first.train_index}
    assert held and not (held & trained)
    assert held == set(first.holdout_groups)


def test_a_small_cohort_degrades_honestly():
    split = split_module.plan_selection_split(
        n_rows=4, labels=[0, 1, 0, 1], group_values=["a", "b", "c", "d"], group_column="subject_key"
    )
    document = builder.selection_document(split.to_manifest())
    assert document["label_supervised_on_full_data"] is True
    assert document["degraded_reason"]


# ----------------------------------------------------------------------
# A generic CSV
# ----------------------------------------------------------------------
def test_a_csv_is_rekeyed_by_volume_and_relabelled_from_the_cohort():
    analysis = _analysis(("v1", "p1", 1, 1), ("v2", "p2", 0, 2))
    csv = pd.DataFrame({"id": ["v2", "v1"], "target": [1, 1], "a": [2.0, 1.0]})
    frame, notes = builder.frame_from_csv(csv, analysis)
    assert frame[builder.VOLUME_KEY].tolist() == ["v1", "v2"]
    assert frame[builder.SUBJECT_KEY].tolist() == ["p1", "p2"]
    # The CSV's own label (1 for v2) is dropped; the cohort's (0) is used.
    assert frame[builder.TARGET_COLUMN].tolist() == [1, 0]
    assert "id" not in frame.columns
    assert notes and "dropped label column" in notes[0]


def test_a_csv_row_outside_the_cohort_is_refused():
    analysis = _analysis(("v1", "p1", 1, 1))
    csv = pd.DataFrame({"id": ["v1", "v9"], "a": [1.0, 2.0]})
    with pytest.raises(builder.FeatureSetBuildError, match="not labelled members"):
        builder.frame_from_csv(csv, analysis)


def test_a_csv_can_have_two_rows_for_two_volumes_of_one_subject():
    """Two volumes of one subject are two legitimate rows, identified by
    their own volume UIDs — not a conflict."""
    analysis = _analysis(("v1", "p1", 1, 1), ("v2", "p1", 0, 2))
    csv = pd.DataFrame({"id": ["v1", "v2"], "a": [1.0, 2.0]})
    frame, _ = builder.frame_from_csv(csv, analysis)
    assert frame[builder.VOLUME_KEY].tolist() == ["v1", "v2"]
    assert frame[builder.SUBJECT_KEY].tolist() == ["p1", "p1"]
    assert frame[builder.TARGET_COLUMN].tolist() == [1, 0]


def test_a_csv_with_two_rows_for_one_volume_is_refused():
    analysis = _analysis(("v1", "p1", 1, 1))
    csv = pd.DataFrame({"id": ["v1", "v1"], "a": [1.0, 2.0]})
    with pytest.raises(builder.FeatureSetBuildError, match="one volume"):
        builder.frame_from_csv(csv, analysis)


def test_a_bare_subject_key_is_ambiguous_when_the_subject_has_two_volumes():
    """A CSV keyed only by subject cannot disambiguate a patient's two
    volumes; it must name the volume instead."""
    analysis = _analysis(("v1", "p1", 1, 1), ("v2", "p1", 0, 2))
    csv = pd.DataFrame({"subject_key": ["p1"], "a": [1.0]})
    with pytest.raises(builder.FeatureSetBuildError, match="no column identifying"):
        builder.frame_from_csv(csv, analysis)
