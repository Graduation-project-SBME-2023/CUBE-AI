"""Recording a modeling comparison."""

from __future__ import annotations

import io
import math

import pytest

pd = pytest.importorskip("pandas")
from agent_system.toolpacks.modeling import results
from agent_system.toolpacks.modeling import evaluation

pytestmark = pytest.mark.unit

DIGEST = "ab" * 32


# ----------------------------------------------------------------------
# Identities
# ----------------------------------------------------------------------
def test_the_default_seed_is_derived_from_the_data():
    seed = results.deterministic_seed(DIGEST)
    assert seed == results.deterministic_seed(DIGEST)
    assert 1 <= seed <= 10_000
    assert results.deterministic_seed("cd" * 32) != seed


def test_the_comparison_key_names_input_and_configuration():
    config = {"fold": 5, "seed": 7, "include_models": None}
    key = results.comparison_key("classification", "top_10", config)
    assert key == results.comparison_key("classification", "top_10", dict(reversed(config.items())))
    assert key.startswith("classification:top_10:")
    assert results.comparison_key("classification", "top_10", {**config, "fold": 10}) != key
    assert results.comparison_key("classification", "all_features", config) != key
    # Keys travel in URL paths and never contain a separator.
    assert "/" not in results.comparison_key("classification", "a/b", config)


def test_fitted_pipelines_live_only_under_the_model_binary_directory():
    """exception 2 is by path and purpose: one directory holds binaries."""
    name = results.model_file_logical_name("classification:top_10:abc", "tuned_model_1")
    assert name.startswith(results.MODEL_BINARY_LOGICAL_ROOT + "/")
    assert name.endswith("/tuned_model_1.pkl")
    assert ".." not in name and not name.startswith("/") and ":" not in name


def test_predictions_are_content_addressed():
    first = results.predictions_logical_key("run", "cmp", "v1", b"a,b\n1,2\n")
    assert first == results.predictions_logical_key("run", "cmp", "v1", b"a,b\n1,2\n")
    assert first != results.predictions_logical_key("run", "cmp", "v1", b"a,b\n1,3\n")


def test_json_safe_values_are_finite_and_native():
    np = pytest.importorskip("numpy")
    cleaned = results.json_safe({"a": float("nan"), "b": np.float64(1.5), "c": np.int64(2), "d": (1, 2)})
    assert cleaned == {"a": None, "b": 1.5, "c": 2, "d": [1, 2]}
    assert results.versioned({"x": 1})["schema_version"] == results.MODEL_RESULT_SCHEMA_VERSION


def test_an_estimator_spec_names_its_class_and_plain_parameters():
    linear_model = pytest.importorskip("sklearn.linear_model")
    spec = results.estimator_spec(linear_model.LogisticRegression(C=0.5, max_iter=50))
    assert spec["estimator"].endswith("LogisticRegression")
    assert spec["params"]["C"] == 0.5 and spec["params"]["max_iter"] == 50


# ----------------------------------------------------------------------
# Honest evaluation labels
# ----------------------------------------------------------------------
def _prepared(**overrides):
    base = dict(
        source_label="top_10",
        cohort_id="c",
        dataset_digest=DIGEST,
        train_path="train.csv",
        holdout_path=None,
        feature_set_id="fs",
        label_supervised_on_full_data=False,
    )
    return results.ModelingInput(**{**base, **overrides})


def test_only_a_blind_holdout_is_a_generalisation_estimate():
    assert results.generalisation_estimate(_prepared(), True) is True
    assert results.evaluation_kind(_prepared(), True) == "selection_holdout"
    # Features chosen with every label: never a clean estimate.
    assert results.generalisation_estimate(_prepared(label_supervised_on_full_data=True), True) is False
    # PyCaret's own random split re-mixes selection-contaminated rows.
    assert results.generalisation_estimate(_prepared(), False) is False
    assert results.evaluation_kind(_prepared(), False) == "random_split"
    generic = _prepared(feature_set_id=None, label_supervised_on_full_data=None)
    assert results.evaluation_kind(generic, True) == "explicit_holdout"


# ----------------------------------------------------------------------
# A file is identified by its recorded digest
# ----------------------------------------------------------------------
def test_a_swapped_model_file_is_refused(tmp_path):
    base = tmp_path / "tuned_model_1"
    (tmp_path / "tuned_model_1.pkl").write_bytes(b"fitted pipeline")
    recorded = {"variant_key": "tuned_model_1", "model_file_digest": results.file_digest(str(base) + ".pkl")}
    assert results.model_file_mismatch(str(base), recorded) is None
    (tmp_path / "tuned_model_1.pkl").write_bytes(b"something else")
    message = results.model_file_mismatch(str(base), recorded)
    assert message and "refusing" in message and "Refit" in message
    # A file that is not a recorded result is not judged here.
    assert results.model_file_mismatch(str(base), None) is None


# ----------------------------------------------------------------------
# The tool reply is built from the recorded rows
# ----------------------------------------------------------------------
def test_the_reply_is_derived_from_the_recorded_comparison():
    recorded = {
        "comparison_key": "classification:top_10:abc",
        "feature_set_id": "fs",
        "dataset_digest": DIGEST,
        "selected_variant_key": "blended_model",
        "variants": [
            {
                "model_result_id": "1",
                "variant_key": "tuned_model_1",
                "algorithm": "LogisticRegression",
                "status": "ready",
                "selected": False,
                "model_file_name": "modeling/models/cmp/tuned_model_1.pkl",
                "predictions_file_id": "p1",
                "split_definition": {"kind": "selection_holdout"},
                "metrics": {"evaluation": {"generalisation_estimate": True}},
            },
            {
                "model_result_id": "2",
                "variant_key": "blended_model",
                "algorithm": "Blended",
                "status": "ready",
                "selected": True,
                "model_file_name": "modeling/models/cmp/blended_model.pkl",
                "predictions_file_id": "p2",
                "split_definition": {"kind": "selection_holdout"},
                "metrics": {"evaluation": {"generalisation_estimate": True}},
            },
        ],
    }
    reply = results.recorded_response(recorded, experiment_name="x", prepared=_prepared(), reused=True)
    assert reply["reused_recorded_results"] is True
    assert reply["selected_variant"] == "blended_model"
    assert reply["final_model_path"].replace("\\", "/").endswith("modeling/models/cmp/blended_model")
    assert reply["model_test_predictions"] == {"tuned_model_1": "p1", "blended_model": "p2"}
    assert reply["exports"]["predictions_blended_model"] == "/files/p2/content?download=true"
    assert reply["selection_holdout_used"] is True
    assert reply["generalisation_estimate"] is True


# ----------------------------------------------------------------------
# Per-case predictions agree with the recorded aggregates
# ----------------------------------------------------------------------
def test_classification_metrics_agree_with_the_published_predictions():
    pytest.importorskip("sklearn")
    predictions = pd.DataFrame(
        {
            "a": [0.1, 0.4, 0.8, 0.9, 0.2, 0.7],
            "target": [0, 0, 1, 1, 0, 1],
            "prediction_label": [0, 1, 1, 1, 0, 0],
            "prediction_score_1": [0.2, 0.6, 0.9, 0.8, 0.1, 0.4],
        }
    )
    recorded = evaluation.holdout_metrics(predictions, predictions["target"])["metrics"]
    # The artifact is `frame.to_csv(index=False)`; re-reading it must give
    # back the same aggregates.
    published = pd.read_csv(io.BytesIO(predictions.to_csv(index=False).encode("utf-8")))
    again = evaluation.holdout_metrics(published, published["target"])["metrics"]
    assert recorded == again
    assert recorded["accuracy"] == pytest.approx(4 / 6, abs=1e-6)
    assert sum(sum(row) for row in recorded["confusion_matrix"]) == len(predictions)


def test_regression_metrics_agree_with_the_published_predictions():
    pytest.importorskip("sklearn")
    predictions = pd.DataFrame(
        {"a": [1.0, 2.0, 3.0, 4.0], "target": [1.0, 2.0, 3.0, 5.0], "prediction_label": [1.1, 1.9, 3.2, 4.5]}
    )
    recorded = evaluation.regression_metrics(predictions)
    published = pd.read_csv(io.BytesIO(predictions.to_csv(index=False).encode("utf-8")))
    assert evaluation.regression_metrics(published) == recorded
    assert math.isfinite(recorded["rmse"]) and recorded["mae"] == pytest.approx(0.225)


# ----------------------------------------------------------------------
# The input contract of the training tools
# ----------------------------------------------------------------------
def test_a_training_run_names_exactly_one_input():
    """Both training tools' input models call this one rule."""
    with pytest.raises(ValueError, match="exactly one"):
        results.check_one_source(None, None, None)
    with pytest.raises(ValueError, match="exactly one"):
        results.check_one_source("fs", "/tmp/x.csv", None)
    with pytest.raises(ValueError, match="carries its own holdout"):
        results.check_one_source("fs", None, "/tmp/h.csv")
    results.check_one_source("fs", None, None)
    results.check_one_source(None, "/tmp/x.csv", "/tmp/h.csv")


def test_the_ui_charts_come_from_the_recorded_rows():
    recorded = {
        "variants": [
            {
                "variant_key": "compare_lr",
                "algorithm": "Logistic Regression",
                "metrics": {"stage": "comparison", "fold": 5, "cv": {"AUC": 0.8}},
            },
            {
                "variant_key": "tuned_model_1",
                "algorithm": "LogisticRegression",
                "metrics": {
                    "stage": "tuned",
                    "cv": {"AUC": 0.82},
                    "evaluation": {"metrics": {"auc": 0.79, "confusion_matrix": [[1, 0], [0, 1]]}},
                },
            },
        ]
    }
    chart = evaluation.chart_data_from_recorded(recorded)
    assert chart["fold"] == 5
    assert chart["comparison_table"] == [{"Model": "Logistic Regression", "AUC": 0.8}]
    assert chart["per_model"]["tuned_model_1"]["holdout_metrics"]["auc"] == 0.79
    assert chart["per_model"]["tuned_model_1"]["confusion_matrix"] == [[1, 0], [0, 1]]
