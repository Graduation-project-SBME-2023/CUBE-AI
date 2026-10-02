"""The recovery path: a lost fitted pipeline refits from its record."""

from __future__ import annotations

import pytest

from agent_system.toolpacks.modeling import refit_core as core
from agent_system.toolpacks.modeling import results

pytestmark = pytest.mark.unit


def test_only_a_saved_single_estimator_variant_is_refittable():
    base = {
        "parameters": {"estimator": "sklearn.linear_model.LogisticRegression", "params": {}},
        "seeds": {"session_id": 7},
        "split_definition": {"kind": "selection_holdout"},
    }
    assert core.refit_refusal(base) is None
    assert "blended" in core.refit_refusal({**base, "parameters": {"members": ["a", "b"]}})
    assert "never saved" in core.refit_refusal({**base, "parameters": {"pycaret_model_id": "lr"}})
    assert "seed" in core.refit_refusal({**base, "seeds": {}})
    assert "base variant" in core.refit_refusal(
        {**base, "split_definition": {"kind": "independent_evaluation"}}
    )


def test_the_task_comes_from_the_comparison_key():
    assert core.task_of("classification:top_10:abc") == "classification"
    assert core.task_of("regression:input:abc") == "regression"
    with pytest.raises(ValueError):
        core.task_of("clustering:x:y")


def test_the_recorded_seed_wins_and_data_is_never_configuration():
    arguments = core.setup_arguments(
        {"target": "target", "session_id": 1, "data": "x", "test_data": "y", "fold": 3}, 42
    )
    assert arguments == {"target": "target", "session_id": 42, "fold": 3}


def test_an_estimator_is_rebuilt_from_its_spec():
    linear_model = pytest.importorskip("sklearn.linear_model")
    spec = results.estimator_spec(linear_model.LogisticRegression(C=0.25, max_iter=77))
    rebuilt = core.build_estimator(spec)
    assert isinstance(rebuilt, linear_model.LogisticRegression)
    assert rebuilt.C == 0.25 and rebuilt.max_iter == 77


def test_agreement_is_measured_case_by_case():
    pd = pytest.importorskip("pandas")
    recorded = pd.DataFrame({"prediction_label": [0, 1, 1, 0]})
    same = core.prediction_agreement(recorded, recorded.copy())
    assert same["identical_predictions"] is True and same["label_agreement"] == 1.0
    changed = core.prediction_agreement(recorded, pd.DataFrame({"prediction_label": [0, 1, 0, 0]}))
    assert changed["identical_predictions"] is False and changed["label_agreement"] == 0.75
    assert core.prediction_agreement(None, recorded)["compared"] is False


def test_train_and_holdout_exports_get_disjoint_indices():
    """Both parts of a feature-set export are indexed from zero; PyCaret
    refuses duplicate indices when it concatenates data and test_data, so the
    training tools and the refit must re-index them first."""
    pd = pytest.importorskip("pandas")
    train = pd.DataFrame({"a": [1, 2, 3]})
    holdout = pd.DataFrame({"a": [4, 5]})
    left, right = results.disjoint_indices(train, holdout)
    assert list(left.index) == [0, 1, 2] and list(right.index) == [3, 4]
    assert pd.concat([left, right]).index.is_unique
    assert list(right["a"]) == [4, 5]


@pytest.mark.slow
def test_a_lost_pipeline_refits_to_the_same_predictions():
    """Fit once as the pipeline would, discard it, refit from the record only."""
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    classification = pytest.importorskip("pycaret.classification")

    rng = np.random.default_rng(0)
    features = rng.normal(size=(60, 3))
    frame = pd.DataFrame(features, columns=["a", "b", "c"])
    frame["target"] = (features[:, 0] + 0.5 * features[:, 1] > 0).astype(int)
    # Two separate exports, both indexed from zero — exactly how the feature
    # set's train and holdout parts arrive — prepared the way the tools do.
    train, holdout = results.disjoint_indices(
        frame.iloc[:45].reset_index(drop=True), frame.iloc[45:].reset_index(drop=True)
    )
    recorded_setup = {"target": "target", "fold": 3, "preprocess": True, "log_experiment": False}
    setup = core.setup_arguments(recorded_setup, 123)

    classification.setup(data=train, test_data=holdout, html=False, verbose=False, **setup)
    original = classification.create_model("lr", verbose=False)
    original_predictions = classification.predict_model(original, verbose=False)
    spec = results.estimator_spec(original)
    del original  # the .pkl is gone; only the record remains

    _, _, refitted = core.fit_from_spec(
        task="classification", train=train, holdout=holdout, setup_kwargs=setup, spec=spec
    )
    agreement = core.prediction_agreement(original_predictions, refitted)
    assert agreement["compared"] and agreement["identical_predictions"], agreement
