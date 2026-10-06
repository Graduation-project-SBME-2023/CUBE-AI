"""The recovery path: a lost fitted pipeline refits from its record, on the
scikit-learn engine — including a variant trained with PyCaret (Phase 7.2)."""

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
    assert "never saved" in core.refit_refusal({**base, "parameters": {"model_id": "lr"}})
    assert "seed" in core.refit_refusal({**base, "seeds": {}})
    assert "base variant" in core.refit_refusal(
        {**base, "split_definition": {"kind": "independent_evaluation"}}
    )


def test_a_pycaret_class_path_is_refused_with_a_clear_error():
    record = {
        "parameters": {"estimator": "pycaret.internal.pipeline.Pipeline", "params": {}},
        "seeds": {"session_id": 7},
        "split_definition": {"kind": "random_split"},
    }
    assert "PyCaret class" in core.refit_refusal(record)


def test_the_task_comes_from_the_comparison_key():
    assert core.task_of("classification:top_10:abc") == "classification"
    assert core.task_of("regression:input:abc") == "regression"
    with pytest.raises(ValueError):
        core.task_of("clustering:x:y")


def test_an_estimator_is_rebuilt_from_its_spec():
    linear_model = pytest.importorskip("sklearn.linear_model")
    spec = results.estimator_spec(linear_model.LogisticRegression(C=0.25, max_iter=77))
    rebuilt = core.build_estimator(spec)
    assert isinstance(rebuilt, linear_model.LogisticRegression)
    assert rebuilt.C == 0.25 and rebuilt.max_iter == 77


def test_the_thread_count_is_replaced_on_rebuild():
    ensemble = pytest.importorskip("sklearn.ensemble")
    spec = results.estimator_spec(ensemble.RandomForestClassifier(n_estimators=7, n_jobs=64))
    rebuilt = core.build_estimator(spec, n_jobs=2)
    assert rebuilt.n_jobs == 2 and rebuilt.n_estimators == 7
    # A class without n_jobs is rebuilt without one.
    tree = pytest.importorskip("sklearn.tree")
    assert core.build_estimator(results.estimator_spec(tree.DecisionTreeClassifier()), n_jobs=2)


def test_list_parameters_survive_the_spec():
    neural = pytest.importorskip("sklearn.neural_network")
    spec = results.estimator_spec(neural.MLPClassifier(hidden_layer_sizes=(50, 25)))
    assert spec["params"]["hidden_layer_sizes"] == [50, 25]
    assert list(core.build_estimator(spec).hidden_layer_sizes) == [50, 25]


def test_a_recorded_pycaret_setup_maps_onto_the_engine_pipeline():
    record = {
        "preprocessing": {
            "setup": {
                "target": "target", "session_id": 1, "fold": 3, "normalize": True,
                "transformation": False, "pca": True, "pca_components": 0.9,
                "ignore_features": ["noise"], "fix_imbalance": True, "log_experiment": False,
            },
            "requested_fix_imbalance": True,
            "effective_fix_imbalance": True,
        }
    }
    config, source = core.preprocessing_config(record)
    assert source == "pycaret_setup"
    assert config.normalize and config.pca and config.pca_components == 0.9
    assert config.ignore_features == ("noise",)
    # SMOTE becomes balanced sample weights.
    assert core.class_balance(record) is True


def test_an_engine_record_keeps_its_own_settings():
    record = {
        "preprocessing": {
            "pipeline": {"config": {"normalize": False, "transformation": True}},
            "class_balance": {"requested": True, "effective": False},
        }
    }
    config, source = core.preprocessing_config(record)
    assert source == "engine" and config.transformation and not config.normalize
    assert core.class_balance(record) is False


def test_agreement_is_measured_case_by_case():
    pd = pytest.importorskip("pandas")
    recorded = pd.DataFrame({"prediction_label": [0, 1, 1, 0]})
    same = core.prediction_agreement(recorded, recorded.copy())
    assert same["identical_predictions"] is True and same["label_agreement"] == 1.0
    changed = core.prediction_agreement(recorded, pd.DataFrame({"prediction_label": [0, 1, 0, 0]}))
    assert changed["identical_predictions"] is False and changed["label_agreement"] == 0.75
    assert core.prediction_agreement(None, recorded)["compared"] is False
    # A PyCaret predictions export named its column "Label".
    legacy = pd.DataFrame({"Label": [0, 1, 1, 0]})
    assert core.prediction_agreement(legacy, recorded)["identical_predictions"] is True


def _frame(np, pd, rows=60, seed=0):
    rng = np.random.default_rng(seed)
    features = rng.normal(size=(rows, 3))
    frame = pd.DataFrame(features, columns=["a", "b", "c"])
    frame["target"] = (features[:, 0] + 0.5 * features[:, 1] > 0).astype(int)
    return frame


def test_a_lost_pipeline_refits_to_the_same_predictions():
    """Fit once as the tools do, discard it, refit from the record only."""
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    pytest.importorskip("sklearn")
    from agent_system.toolpacks.modeling.engine import dataset, estimators, fitting
    from agent_system.toolpacks.modeling.engine.preprocessing import (
        PreprocessingConfig,
        build_pipeline,
        pipeline_document,
    )

    frame = _frame(np, pd)
    # Two separate exports, both indexed from zero — exactly how the feature
    # set's train and holdout parts arrive.
    train = frame.iloc[:45].reset_index(drop=True)
    holdout = frame.iloc[45:].reset_index(drop=True)
    config = PreprocessingConfig(normalize=True)
    data = dataset.build("classification", train, holdout, config)
    original = build_pipeline(estimators.build("classification", "lr", seed=123), data.columns, config)
    fitting.fit(original, data.X_train, data.y_train, balance=True)
    original_predictions = dataset.predictions_frame(
        "classification", original, data.eval_frame, data.features, data.classes
    )
    record = {
        "parameters": results.estimator_spec(original),
        "preprocessing": {
            "pipeline": pipeline_document(data.columns, config),
            "class_balance": {"requested": True, "effective": True},
        },
        "seeds": {"session_id": 123},
        "split_definition": {"kind": "selection_holdout"},
    }
    del original  # the .pkl is gone; only the record remains

    _, _, refitted = core.fit_from_record(task="classification", train=train, holdout=holdout, result=record)
    agreement = core.prediction_agreement(original_predictions, refitted)
    assert agreement["compared"] and agreement["identical_predictions"], agreement


def test_a_random_split_is_reproduced_from_the_seed():
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    pytest.importorskip("sklearn")
    from agent_system.toolpacks.modeling.engine import cv

    frame = _frame(np, pd, rows=40)
    _, evaluated, _ = cv.random_split("classification", frame, target="target", seed=9)
    record = {"seeds": {"session_id": 9}, "split_definition": {"stratify": True, "shuffle": True}}
    _, again = core.evaluation_rows("classification", frame, None, record)
    assert evaluated.reset_index(drop=True).equals(again)
