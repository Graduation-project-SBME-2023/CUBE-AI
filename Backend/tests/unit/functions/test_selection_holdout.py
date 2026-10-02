"""The modeling tools must evaluate on the rows feature selection withheld.

If this resolution silently returns None, PyCaret re-splits at random and the
selection leak is back with nothing in the output to show it.
"""

from __future__ import annotations

import pytest

from agent_system.toolpacks.modeling import holdout as module


@pytest.mark.unit
def test_sibling_holdout_is_found_next_to_the_full_export(tmp_path):
    full = tmp_path / "top_10_features.csv"
    holdout = tmp_path / "top_10_features_holdout.csv"
    full.write_text("a,target\n1,0\n")
    holdout.write_text("a,target\n2,1\n")

    assert module.resolve_selection_holdout(str(full)) == str(holdout)


@pytest.mark.unit
def test_train_file_resolves_to_its_own_holdout(tmp_path):
    train = tmp_path / "top_10_features_train.csv"
    holdout = tmp_path / "top_10_features_holdout.csv"
    train.write_text("a,target\n1,0\n")
    holdout.write_text("a,target\n2,1\n")

    assert module.resolve_selection_holdout(str(train)) == str(holdout)


@pytest.mark.unit
def test_missing_sibling_returns_none_rather_than_a_path_that_does_not_exist(tmp_path):
    full = tmp_path / "top_10_features.csv"
    full.write_text("a,target\n1,0\n")

    assert module.resolve_selection_holdout(str(full)) is None


@pytest.mark.unit
def test_explicit_path_wins_over_the_sibling(tmp_path):
    full = tmp_path / "top_10_features.csv"
    sibling = tmp_path / "top_10_features_holdout.csv"
    explicit = tmp_path / "chosen.csv"
    for f in (full, sibling, explicit):
        f.write_text("a,target\n1,0\n")

    assert module.resolve_selection_holdout(str(full), str(explicit)) == str(explicit)


@pytest.mark.unit
def test_explicit_path_that_does_not_exist_returns_none(tmp_path):
    full = tmp_path / "top_10_features.csv"
    sibling = tmp_path / "top_10_features_holdout.csv"
    full.write_text("a,target\n1,0\n")
    sibling.write_text("a,target\n2,1\n")

    # Falling back to the sibling would silently ignore the caller's instruction.
    assert module.resolve_selection_holdout(str(full), str(tmp_path / "nope.csv")) is None


@pytest.mark.unit
def test_a_holdout_file_has_no_holdout_of_its_own(tmp_path):
    holdout = tmp_path / "top_10_features_holdout.csv"
    holdout.write_text("a,target\n2,1\n")

    assert module.resolve_selection_holdout(str(holdout)) is None


@pytest.mark.unit
@pytest.mark.parametrize("value", ["", None])
def test_blank_input_path_is_handled(value):
    assert module.resolve_selection_holdout(value) is None
