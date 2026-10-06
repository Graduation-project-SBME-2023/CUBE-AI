"""The modeling engine without PyCaret (merged plan, Phase 7.0). Synthetic data only."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("sklearn")

from agent_system.toolpacks.common import settings  # noqa: E402
from agent_system.toolpacks.modeling.engine import (  # noqa: E402
    blend,
    compare,
    cv,
    dataset,
    estimators,
    fitting,
    metrics,
    persistence,
    plots,
    tune,
)
from agent_system.toolpacks.modeling.engine.preprocessing import (  # noqa: E402
    PreprocessingConfig,
    build_pipeline,
    feature_columns,
    pipeline_document,
)

pytestmark = pytest.mark.unit

BACKEND_ROOT = Path(__file__).resolve().parents[4]
TOOLPACKS_ROOT = BACKEND_ROOT / "agent_system" / "toolpacks"


def _classification(rows=60, seed=0, labels=(0, 1)):
    rng = np.random.default_rng(seed)
    features = rng.normal(size=(rows, 4))
    frame = pd.DataFrame(features, columns=["a", "b", "c", "d"])
    frame["target"] = np.where(features[:, 0] + 0.5 * features[:, 1] > 0, labels[1], labels[0])
    return frame


def _regression(rows=60, seed=0):
    rng = np.random.default_rng(seed)
    features = rng.normal(size=(rows, 3))
    frame = pd.DataFrame(features, columns=["a", "b", "c"])
    frame["target"] = 3 * features[:, 0] - features[:, 1] + rng.normal(scale=0.1, size=rows) + 10
    return frame


def _dataset(task, frame, config=PreprocessingConfig()):
    train, holdout, _ = cv.random_split(task, frame, target="target", seed=1)
    return dataset.build(task, train.reset_index(drop=True), holdout.reset_index(drop=True), config)


def _compare(task, ds, ids, seed=7):
    folds = cv.clamp_folds(task, 3, pd.Series(ds.y_train))
    splitter = cv.splitter(task, folds, seed)
    candidates = [estimators.entry(task, model_id) for model_id in ids]
    result = compare.compare(
        task, candidates, ds.X_train, ds.y_train, cv=splitter,
        make_pipeline=lambda e: build_pipeline(estimators.instantiate(e, seed=seed), ds.columns, ds.config),
        n_classes=ds.n_classes,
    )
    return result, splitter


# ----------------------------------------------------------------------
# Registries
# ----------------------------------------------------------------------
def test_the_registries_keep_the_pycaret_model_ids():
    for model_id in ("lr", "ridge", "knn", "nb", "dt", "rf", "et", "ada", "gbc", "svm", "lda", "mlp",
                     "lightgbm", "xgboost", "dummy"):
        assert model_id in estimators.CLASSIFIERS, model_id
    for model_id in ("lr", "ridge", "lasso", "en", "knn", "dt", "rf", "et", "ada", "gbr", "svm",
                     "lightgbm", "xgboost", "dummy"):
        assert model_id in estimators.REGRESSORS, model_id
    for registry in (estimators.CLASSIFIERS, estimators.REGRESSORS):
        assert "catboost" not in registry
        assert not any("catboost" in entry.class_path for entry in registry.values())


@pytest.mark.parametrize("task", estimators.TASKS)
def test_every_installed_entry_builds_with_its_search_space(task):
    for entry in estimators.registry(task).values():
        if not entry.available():
            continue
        model = estimators.instantiate(entry, seed=3, n_jobs=1)
        params = model.get_params()
        assert set(entry.search_space) <= set(params), entry.model_id
        if entry.seeded:
            assert params["random_state"] == 3, entry.model_id


def test_an_unknown_model_id_is_an_error_not_a_silent_skip():
    with pytest.raises(ValueError, match="catboost"):
        estimators.select("classification", include=["lr", "catboost"])
    with pytest.raises(ValueError):
        estimators.select("regression", exclude=["nope"])


def test_the_default_and_small_cohort_candidate_sets():
    default = {e.model_id for e in estimators.select("classification")}
    assert "mlp" not in default and "rbfsvm" not in default and "lr" in default
    small = [e.model_id for e in estimators.select("classification", small_cohort=True, exclude=["knn"])]
    assert small == [m for m in estimators.SMALL_COHORT_CLASSIFIERS if m != "knn"]


def test_n_jobs_is_capped_by_compute_cpu_threads(monkeypatch):
    monkeypatch.setenv("COMPUTE_CPU_THREADS", "2")
    assert settings.compute_cpu_threads() == min(2, os.cpu_count() or 1)
    monkeypatch.setenv("COMPUTE_CPU_THREADS", "100000")
    assert settings.compute_cpu_threads() == (os.cpu_count() or 1)
    monkeypatch.setenv("COMPUTE_CPU_THREADS", "zero")
    assert settings.compute_cpu_threads() == (os.cpu_count() or 1)
    forest = estimators.build("classification", "rf", seed=1, n_jobs=settings.compute_cpu_threads())
    assert forest.n_jobs == settings.compute_cpu_threads()


# ----------------------------------------------------------------------
# Preprocessing and folds
# ----------------------------------------------------------------------
def test_columns_are_typed_and_left_out_as_configured():
    frame = pd.DataFrame({
        "age": [1.0, 2.0, None], "site": ["a", "b", "a"], "grade": [1, 2, 3],
        "scan_date": ["x", "y", "z"], "noise": [0, 0, 0], "target": [0, 1, 0],
    })
    config = PreprocessingConfig(
        categorical_features=("grade",), date_features=("scan_date",), ignore_features=("noise",)
    )
    columns = feature_columns(frame, config)
    assert columns.numeric == ["age"]
    assert columns.categorical == ["site", "grade"]
    assert columns.dropped == ["scan_date", "noise"]
    with pytest.raises(ValueError, match="not in the data"):
        feature_columns(frame, PreprocessingConfig(numeric_features=("missing",)))


def test_the_pipeline_imputes_encodes_and_scales():
    frame = pd.DataFrame({
        "age": [1.0, np.nan, 3.0, 4.0], "site": ["a", np.nan, "b", "a"], "target": [0, 1, 0, 1],
    })
    config = PreprocessingConfig(normalize=True)
    columns = feature_columns(frame, config)
    pipeline = build_pipeline(estimators.build("classification", "lr", seed=0), columns, config)
    pipeline.fit(frame[columns.used], frame["target"])
    transformed = pipeline[:-1].transform(frame[columns.used])
    assert not np.isnan(transformed).any()
    assert plots.feature_names(pipeline) == ["age", "site_a", "site_b"]
    document = pipeline_document(columns, config)
    assert document["numeric_steps"] == ["impute_median", "standard_scale"]
    assert document["categorical_steps"] == ["impute_most_frequent", "one_hot"]
    assert document["engine"] == "sklearn" and document["config"]["normalize"] is True


def test_the_fold_count_is_clamped_to_the_rarest_class():
    y = pd.Series([0] * 10 + [1] * 3)
    assert cv.clamp_folds("classification", 5, y) == 3
    assert cv.clamp_folds("classification", 5, pd.Series([0] * 10 + [1])) == 1
    assert cv.clamp_folds("regression", 10, pd.Series(range(4))) == 4
    assert cv.clamp_folds("classification", 1, pd.Series([0] * 5 + [1] * 5)) == 2


def test_string_labels_are_encoded_and_decoded():
    pytest.importorskip("xgboost")
    ds = _dataset("classification", _classification(labels=("benign", "malignant")))
    assert ds.classes == ["benign", "malignant"]
    model = build_pipeline(estimators.build("classification", "xgboost", seed=1), ds.columns, ds.config)
    fitting.fit(model, ds.X_train, ds.y_train)
    frame = dataset.predictions_frame("classification", model, ds.eval_frame, ds.features, ds.classes)
    assert set(frame["prediction_label"]) <= {"benign", "malignant"}
    assert {"prediction_score_benign", "prediction_score_malignant"} <= set(frame.columns)


# ----------------------------------------------------------------------
# Compare, tune, blend
# ----------------------------------------------------------------------
def test_the_metric_columns_keep_their_names():
    """Golden: the comparison table columns the result shape and ModelingView read."""
    ds = _dataset("classification", _classification())
    result, _ = _compare("classification", ds, ["lr", "dt"])
    assert list(result.rows[0]) == ["model_id", "Model", "Accuracy", "AUC", "Recall", "Prec.", "F1",
                                    "Kappa", "MCC", "TT (Sec)"]
    ds = _dataset("regression", _regression())
    result, _ = _compare("regression", ds, ["lr", "dt"])
    assert list(result.rows[0]) == ["model_id", "Model", "MAE", "MSE", "RMSE", "R2", "RMSLE", "MAPE",
                                    "TT (Sec)"]
    assert result.rows[0]["model_id"] == "lr"  # a linear target: lr wins on R2


def test_the_same_seed_gives_the_same_table_and_selection():
    ds = _dataset("classification", _classification())

    def table():
        result, _ = _compare("classification", ds, ["lr", "dt", "rf", "nb"])
        return [{k: v for k, v in row.items() if k != "TT (Sec)"} for row in result.rows]

    first, second = table(), table()
    assert first == second
    assert first[0]["Accuracy"] >= first[-1]["Accuracy"]


def test_a_failing_candidate_is_left_out_and_named():
    ds = _dataset("classification", _classification())
    broken = estimators.Estimator("broken", "Broken", "sklearn.linear_model.LogisticRegression",
                                  {"C": -1.0})
    folds = cv.splitter("classification", 3, 0)
    result = compare.compare(
        "classification", [estimators.entry("classification", "lr"), broken], ds.X_train, ds.y_train,
        cv=folds, make_pipeline=lambda e: build_pipeline(estimators.instantiate(e, seed=0), ds.columns, ds.config),
        n_classes=2,
    )
    assert [row["model_id"] for row in result.rows] == ["lr"]
    assert "broken" in result.failures


def test_tuning_is_seeded_and_keeps_the_better_model():
    ds = _dataset("classification", _classification())
    result, splitter = _compare("classification", ds, ["dt"])
    entry = estimators.entry("classification", "dt")
    base = {k: v for k, v in result.rows[0].items() if k not in ("model_id", "Model", "TT (Sec)")}

    def run():
        model = build_pipeline(estimators.instantiate(entry, seed=7), ds.columns, ds.config)
        return tune.tune("classification", model, entry, ds.X_train, ds.y_train,
                         cv=splitter, seed=7, base_cv=base, n_classes=2)

    first, second = run(), run()
    assert first.params == second.params and first.cv == second.cv
    assert first.tried == min(tune.N_ITER, tune.search_size(dict(entry.search_space)))
    assert first.cv["Accuracy"] >= base["Accuracy"]
    # An unbeatable base keeps the untuned model.
    kept = tune.tune("classification", build_pipeline(estimators.instantiate(entry, seed=7), ds.columns,
                     ds.config), entry, ds.X_train, ds.y_train, cv=splitter, seed=7,
                     base_cv={**base, "Accuracy": 2.0}, n_classes=2)
    assert kept.improved is False and kept.params == {}


def test_a_blend_votes_soft_only_when_every_member_gives_probabilities():
    ds = _dataset("classification", _classification())

    def member(model_id):
        return build_pipeline(estimators.build("classification", model_id, seed=0), ds.columns, ds.config)

    soft = blend.blend("classification", [("tuned_model_1", member("lr")), ("tuned_model_2", member("rf"))])
    assert soft.voting == "soft"
    hard = blend.blend("classification", [("tuned_model_1", member("lr")), ("tuned_model_2", member("ridge"))])
    assert hard.voting == "hard"
    fitting.fit(hard, ds.X_train, ds.y_train)
    assert metrics.scores_of(hard, ds.X_eval) is None
    assert set(fitting.score("classification", hard, ds.X_eval, ds.y_eval, 2)) == set(metrics.CLASSIFICATION_METRICS)
    with pytest.raises(ValueError):
        blend.blend("classification", [("only", member("lr"))])


def test_balanced_weights_reach_only_estimators_that_take_them():
    y = np.array([0] * 9 + [1] * 3)
    ds = _dataset("classification", _classification())
    lr = build_pipeline(estimators.build("classification", "lr", seed=0), ds.columns, ds.config)
    weights = fitting.fit_params(lr, y, balance=True)["model__sample_weight"]
    assert weights[-1] == pytest.approx(3 * weights[0])
    knn = build_pipeline(estimators.build("classification", "knn", seed=0), ds.columns, ds.config)
    assert fitting.fit_params(knn, y, balance=True) == {}
    assert fitting.fit_params(lr, y, balance=False) == {}


def test_regression_scores_follow_the_table_definitions():
    scores = metrics.regression_scores([1.0, 2.0, 4.0], [1.0, 2.0, 2.0])
    assert scores["MAE"] == pytest.approx(2 / 3, abs=1e-6)
    assert scores["RMSE"] == pytest.approx(np.sqrt(4 / 3), abs=1e-6)
    assert scores["MAPE"] == pytest.approx(0.5 / 3, abs=1e-6)
    assert metrics.regression_scores([-1.0, 2.0], [0.0, 2.0])["RMSLE"] is None


def test_the_engine_prints_nothing(capfd):
    ds = _dataset("classification", _classification())
    result, splitter = _compare("classification", ds, ["lr", "rf", "svm"])
    entry = estimators.entry("classification", "rf")
    tuned = tune.tune("classification", build_pipeline(estimators.instantiate(entry, seed=1), ds.columns,
                      ds.config), entry, ds.X_train, ds.y_train, cv=splitter, seed=1, base_cv={}, n_iter=2,
                      n_classes=2)
    fitting.fit(tuned.model, ds.X_train, ds.y_train)
    captured = capfd.readouterr()
    assert captured.out == "" and captured.err == ""


# ----------------------------------------------------------------------
# Figures and persistence
# ----------------------------------------------------------------------
def test_classification_figures(tmp_path):
    ds = _dataset("classification", _classification())
    model = build_pipeline(estimators.build("classification", "rf", seed=0), ds.columns, ds.config)
    fitting.fit(model, ds.X_train, ds.y_train)
    paths, skipped = plots.draw_all("classification", model, ds.X_eval, ds.y_eval, str(tmp_path),
                                    classes=ds.classes, tree=True)
    names = {Path(path).name for path in paths}
    assert {"AUC.png", "Confusion Matrix.png", "Feature Importance.png"} <= names
    try:
        import shap  # noqa: F401
    except ImportError:
        assert any(reason.startswith("shap_summary") for reason in skipped)
    else:
        assert "SHAP Summary.png" in names, skipped
    assert all(Path(path).stat().st_size > 0 for path in paths)


def test_regression_figures(tmp_path):
    ds = _dataset("regression", _regression())
    model = build_pipeline(estimators.build("regression", "ridge", seed=0), ds.columns, ds.config)
    fitting.fit(model, ds.X_train, ds.y_train)
    paths, _ = plots.draw_all("regression", model, ds.X_eval, ds.y_eval, str(tmp_path))
    assert {Path(path).name for path in paths} == {"Residuals.png", "Prediction Error.png", "Feature Importance.png"}


def test_the_bundle_is_written_atomically_and_reloads(tmp_path):
    ds = _dataset("classification", _classification())
    model = build_pipeline(estimators.build("classification", "lr", seed=0), ds.columns, ds.config)
    fitting.fit(model, ds.X_train, ds.y_train)
    target = tmp_path / "models" / "tuned_model_1.pkl"
    persistence.save_bundle(persistence.make_bundle("classification", model, ds.features, ds.classes), target)
    assert [p.name for p in target.parent.iterdir()] == ["tuned_model_1.pkl"]  # no temp file left
    bundle = persistence.load_bundle(target)
    assert bundle["engine"] == "sklearn" and bundle["features"] == ds.features and bundle["classes"] == [0, 1]
    assert (bundle["pipeline"].predict(ds.X_eval) == model.predict(ds.X_eval)).all()


def test_a_file_that_is_not_an_engine_bundle_is_legacy(tmp_path):
    import joblib

    joblib.dump({"some": "pycaret pipeline"}, tmp_path / "old.pkl")
    with pytest.raises(persistence.LegacyModelError):
        persistence.load_bundle(tmp_path / "old.pkl")
    assert persistence.is_engine_record({"environment": {"engine": "sklearn"}})
    assert not persistence.is_engine_record({"environment": {"packages": {"pycaret": "3.3.2"}}})
    assert not persistence.is_engine_record(None)
    assert persistence.environment()["engine"] == "sklearn"
    assert "pycaret" not in persistence.environment()["packages"]


# ----------------------------------------------------------------------
# Source scan
# ----------------------------------------------------------------------
def test_nothing_under_toolpacks_imports_pycaret():
    pattern = re.compile(r"^\s*(import|from)\s+pycaret\b|import_module\(\s*f?[\"']pycaret", re.M)
    offenders = [
        str(path.relative_to(TOOLPACKS_ROOT))
        for path in TOOLPACKS_ROOT.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders


def test_no_tool_name_starts_with_pycaret():
    from agent_system.toolkit import catalog as catalog_module

    names = {spec.name for spec in catalog_module.build().tools}
    assert not [name for name in names if name.startswith("pycaret_")]
    assert {"train_classifier", "train_regressor", "run_classifier_inference",
            "run_regression_inference", "refit_model"} <= names
