"""run_modeling's arguments and the fixed rule behind task="auto"."""

from __future__ import annotations

import math
from typing import Any

import pytest

pd = pytest.importorskip("pandas")

from pydantic import ValidationError  # noqa: E402

from agent_system.toolpacks.modeling.engine.task import MAX_CLASSES, infer_task  # noqa: E402
from agent_system.toolpacks.modeling.run_modeling import MAX_SEED, RunModelingInput  # noqa: E402

pytestmark = pytest.mark.unit

SET_A = "44444444-4444-4444-8444-444444444444"
SET_B = "55555555-5555-4555-8555-555555555555"


def _input(**overrides: Any) -> RunModelingInput:
    fields: dict[str, Any] = {"feature_set_ids": [SET_A]}
    fields.update(overrides)
    return RunModelingInput(**fields)


# --- the auto rule, one row per case


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["yes", "no", "yes"], "classification"),
        (["low", "mid", "high"], "classification"),
        ([True, False, True], "classification"),
        ([0, 1, 1, 0], "classification"),
        ([1, 2, 3], "classification"),
        ([0.0, 1.0, 1.0], "classification"),  # labels stored as floats
        (list(range(MAX_CLASSES)), "classification"),  # exactly the limit
        (list(range(MAX_CLASSES + 1)), "regression"),  # one more: a count, not labels
        ([0.5, 1.25, 3.0], "regression"),
        ([10.0, 20.5, 31.2, 40.0], "regression"),
        ([0, 1, None, 1], "classification"),  # missing values are left out
        ([1.5, math.nan, 2.5], "regression"),
        ([1.0, math.inf, 2.0], "regression"),  # inf is not a whole number
        (["1", "2", "1"], "classification"),  # numbers as text are still text
    ],
)
def test_the_auto_rule(values: list[Any], expected: str) -> None:
    assert infer_task(pd.Series(values, name="label")) == expected


def test_a_categorical_column_is_a_classification() -> None:
    assert infer_task(pd.Series(["a", "b"], dtype="category")) == "classification"


@pytest.mark.parametrize("values", [[], [None, None], [math.nan]])
def test_a_column_without_values_cannot_decide(values: list[Any]) -> None:
    with pytest.raises(ValueError, match="no values"):
        infer_task(pd.Series(values, name="label", dtype="object"))


# --- the input model


def test_the_defaults() -> None:
    params = _input()
    assert params.target == "has_target_disease"
    assert params.task == "auto"
    assert params.comparisons is None
    assert (params.folds, params.seed, params.include_inference) == (5, None, True)


def test_feature_set_ids_are_canonical_uuids() -> None:
    params = _input(feature_set_ids=[SET_A.upper(), SET_B])
    assert params.feature_set_ids == [SET_A, SET_B]


def test_the_arguments_are_flat_so_a_port_can_fill_feature_set_ids() -> None:
    # A graph node's input port fills one top-level argument by name.
    assert "feature_set_ids" in RunModelingInput.model_fields
    assert "params" not in RunModelingInput.model_fields


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"feature_set_ids": []}, "at least 1 item"),
        ({"feature_set_ids": ["top_10"]}, "not a UUID"),
        ({"feature_set_ids": [SET_A, SET_A.upper()]}, "listed twice"),
        ({"target": ""}, "at least 1 character"),
        ({"target": "   "}, "at least 1 character"),  # stripped first
        ({"task": "clustering"}, "classification"),
        ({"comparisons": []}, "at least one estimator"),
        ({"comparisons": ["lr", "catboost"]}, "unknown estimator id 'catboost'"),
        ({"comparisons": ["rf", "rf"]}, "listed twice"),
        ({"task": "classification", "comparisons": ["lasso"]}, "not classification estimators"),
        ({"task": "regression", "comparisons": ["nb"]}, "not regression estimators"),
        ({"folds": 1}, "greater than or equal to 2"),
        ({"folds": 21}, "less than or equal to 20"),
        ({"seed": -1}, "greater than or equal to 0"),
        ({"seed": MAX_SEED + 1}, "less than or equal to"),
        ({"pca": True}, "Extra inputs"),
    ],
)
def test_bad_arguments_are_refused(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _input(**overrides)


def test_comparisons_of_either_task_are_accepted_under_auto() -> None:
    # The task is not known yet; the handler checks them once it is.
    assert _input(comparisons=["lasso", "nb"]).comparisons == ["lasso", "nb"]


# --- resolve_task


def test_an_explicit_task_is_kept_whatever_the_target_looks_like() -> None:
    continuous = pd.Series([0.1, 0.7, 2.3])
    assert _input(task="classification").resolve_task(continuous) == "classification"
    assert _input(task="regression").resolve_task(pd.Series(["a", "b"])) == "regression"


def test_auto_applies_the_rule() -> None:
    assert _input().resolve_task(pd.Series([0, 1, 0])) == "classification"
    assert _input().resolve_task(pd.Series([0.1, 0.7, 2.3])) == "regression"
