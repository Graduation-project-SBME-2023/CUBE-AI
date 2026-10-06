"""The 5 modeling tools on the scikit-learn engine, called through dispatch
against a mocked Application API (merged plan, Phases 7.1 and 7.2).

Synthetic data only. The fake API keeps what the tools publish and record, so
a test can train, predict with the recorded model and refit it.
"""

from __future__ import annotations

import io
import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("sklearn")

from agent_system.toolkit.results import is_error_result  # noqa: E402
from agent_system.toolpacks.modeling.classification import (  # noqa: E402
    run_classifier_inference,
    train_classifier,
)
from agent_system.toolpacks.modeling.engine import persistence  # noqa: E402
from agent_system.toolpacks.modeling.refit import refit_model  # noqa: E402
from agent_system.toolpacks.modeling.regression import (  # noqa: E402
    run_regression_inference,
    train_regressor,
)
from tests.support.toolkit_fixtures import TEST_SESSION_ID, call_tool, make_context  # noqa: E402

pytestmark = pytest.mark.unit

RUN = "22222222-2222-4222-8222-222222222222"
ATTEMPT = "33333333-3333-4333-8333-333333333333"
FEATURE_SET = "44444444-4444-4444-8444-444444444444"
COHORT = "55555555-5555-4555-8555-555555555555"


def _frame(task: str, rows: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    features = rng.normal(size=(rows, 4))
    frame = pd.DataFrame(features, columns=["f1", "f2", "f3", "f4"])
    if task == "classification":
        frame["target"] = (features[:, 0] + 0.7 * features[:, 1] > 0).astype(int)
    else:
        frame["target"] = 2 * features[:, 0] - features[:, 2] + rng.normal(scale=0.1, size=rows) + 5
    return frame


def _csv(frame: pd.DataFrame) -> str:
    return frame.to_csv(index=False)


class FakeApplicationApi:
    """The routes the modeling tools use, with the state they write."""

    def __init__(self, router: Any, task: str = "classification") -> None:
        self.files: dict[str, bytes] = {}
        self.comparisons: dict[str, dict[str, Any]] = {}
        self.results: dict[str, dict[str, Any]] = {}
        self.puts: list[dict[str, Any]] = []
        everything = _frame(task, 48, seed=1)
        self.train, self.holdout = everything.iloc[:36], everything.iloc[36:]
        self.export = {"all": _csv(everything), "train": _csv(self.train), "holdout": _csv(self.holdout)}

        router.get(f"/feature-sets/{FEATURE_SET}").respond(json={
            "feature_set_id": FEATURE_SET, "status": "frozen", "set_key": "top_4", "cohort_id": COHORT,
            "selection": {"holdout_groups": ["p37", "p38"], "label_supervised_on_full_data": False},
        })
        router.get(f"/feature-sets/{FEATURE_SET}/export").mock(side_effect=self._export)
        router.get(url__regex=r"/model-results/run/[^/]+/[^/]+$").mock(side_effect=self._get_comparison)
        router.put(url__regex=r"/model-results/run/[^/]+/[^/]+$").mock(side_effect=self._put_comparison)
        router.get(f"/model-results/session/{TEST_SESSION_ID}").mock(
            side_effect=lambda request: httpx.Response(200, json=list(self.results.values()))
        )
        router.get(url__regex=r"/model-results/[0-9a-f-]{36}$").mock(side_effect=self._get_result)
        router.post("/files/").mock(side_effect=self._publish)
        router.get(url__regex=r"/files/[0-9a-f-]{36}/content").mock(side_effect=self._content)
        router.get(f"/cohorts/session/{TEST_SESSION_ID}/population").respond(json={"cohort_id": COHORT})

    def _export(self, request: httpx.Request) -> httpx.Response:
        part = request.url.params.get("part", "all")
        return httpx.Response(200, text=self.export[part], headers={"content-type": "text/csv"})

    def _get_comparison(self, request: httpx.Request) -> httpx.Response:
        from urllib.parse import unquote

        recorded = self.comparisons.get(unquote(request.url.path.rsplit("/", 1)[-1]))
        return httpx.Response(200, json=recorded) if recorded else httpx.Response(404, json={"detail": "x"})

    def _put_comparison(self, request: httpx.Request) -> httpx.Response:
        from urllib.parse import unquote

        key = unquote(request.url.path.rsplit("/", 1)[-1])
        payload = json.loads(request.content)
        self.puts.append(payload)
        shared = {k: v for k, v in payload.items() if k != "variants"}
        variants = []
        for variant in payload["variants"]:
            row = {**shared, **variant, "model_result_id": str(uuid.uuid4()), "comparison_key": key,
                   "created_by_run_id": RUN}
            self.results[row["model_result_id"]] = row
            variants.append(row)
        recorded = {
            "comparison_key": key,
            "feature_set_id": payload.get("feature_set_id"),
            "input_file_id": payload.get("input_file_id"),
            "dataset_digest": payload["dataset_digest"],
            "selected_variant_key": next((v["variant_key"] for v in variants if v["selected"]), None),
            "variants": variants,
        }
        self.comparisons.setdefault(key, recorded)
        return httpx.Response(200, json=recorded)

    def _get_result(self, request: httpx.Request) -> httpx.Response:
        row = self.results.get(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=row) if row else httpx.Response(404, json={"detail": "x"})

    def _publish(self, request: httpx.Request) -> httpx.Response:
        file_id = str(uuid.uuid4())
        body = request.content
        # The multipart body holds the file; keep the CSV part for predictions.
        marker = b"\r\n\r\n"
        parts = body.split(b"--" + request.headers["content-type"].split("boundary=")[1].encode())
        for part in parts:
            if b'name="file"' in part:
                self.files[file_id] = part.split(marker, 1)[1].rsplit(b"\r\n", 1)[0]
        logical_key = next(
            (p.split(marker, 1)[1].rsplit(b"\r\n", 1)[0].decode() for p in parts if b'name="logical_key"' in p),
            "x",
        )
        return httpx.Response(200, json={"file_id": file_id, "logical_key": logical_key,
                                         "display_filename": "f"})

    def _content(self, request: httpx.Request) -> httpx.Response:
        file_id = request.url.path.split("/")[2]
        return httpx.Response(200, content=self.files[file_id], headers={"content-type": "text/csv"})


@pytest.fixture
def ctx(workspace_dir: Path):
    return make_context(run_id=RUN, attempt_id=ATTEMPT, workspace_dir=workspace_dir,
                        agent_type="modeling_agent")


def _train_args(**extra):
    return {"params": {"feature_set_id": FEATURE_SET, "fold": 3, "n_select": 2,
                       "include_models": "lr,dt,nb", **extra}}


def _ok(result):
    assert not is_error_result(result), result
    return json.loads(result)


def test_train_classifier_records_a_comparison_and_replays_it(fake_domain_api, ctx):
    api = FakeApplicationApi(fake_domain_api)
    reply = _ok(call_tool(train_classifier, _train_args(), ctx))

    assert reply["status"] == "success" and reply["reused_recorded_results"] is False
    keys = {row["variant_key"] for row in reply["model_results"]}
    assert {"compare_lr", "compare_dt", "compare_nb", "tuned_model_1", "tuned_model_2", "blended_model"} <= keys
    assert reply["selected_variant"] == "blended_model"
    assert reply["selection_holdout_used"] is True and reply["generalisation_estimate"] is True
    table = reply["chart_data"]["comparison_table"]
    assert {"Model", "Accuracy", "AUC", "Recall", "Prec.", "F1", "Kappa", "MCC"} <= set(table[0])
    assert reply["figures"], "every kept variant publishes its figures"

    payload = api.puts[0]
    assert payload["environment"]["engine"] == "sklearn"
    assert "pycaret" not in payload["environment"]["packages"]
    assert payload["preprocessing"]["pipeline"]["engine"] == "sklearn"
    assert payload["preprocessing"]["class_balance"]["method"] == "balanced_sample_weight"
    assert payload["split_definition"]["kind"] == "selection_holdout"
    assert payload["split_definition"]["holdout_rows"] == len(api.holdout)
    blended = next(v for v in payload["variants"] if v["variant_key"] == "blended_model")
    assert blended["parameters"]["members"] == ["tuned_model_1", "tuned_model_2"]
    tuned = next(v for v in payload["variants"] if v["variant_key"] == "tuned_model_1")
    assert tuned["parameters"]["estimator"].startswith("sklearn.")
    assert tuned["metrics"]["evaluation"]["n_cases"] == len(api.holdout)

    bundle = persistence.load_bundle(reply["final_model_path"] + ".pkl")
    assert bundle["task"] == "classification" and bundle["features"] == ["f1", "f2", "f3", "f4"]

    again = _ok(call_tool(train_classifier, _train_args(), ctx))
    assert again["reused_recorded_results"] is True and len(api.puts) == 1
    assert again["comparison_key"] == reply["comparison_key"]


def test_an_unknown_model_id_is_an_error_result(fake_domain_api, ctx):
    FakeApplicationApi(fake_domain_api)
    result = call_tool(train_classifier, _train_args(include_models="lr,catboost"), ctx)
    assert is_error_result(result) and result["error_code"] == "invalid_model_id"


def test_one_class_is_an_error_result(fake_domain_api, ctx):
    api = FakeApplicationApi(fake_domain_api)
    single = _frame("classification", 20, seed=3).assign(target=1)
    api.export.update(all=_csv(single), train=_csv(single.iloc[:15]), holdout=_csv(single.iloc[15:]))
    result = call_tool(train_classifier, _train_args(), ctx)
    assert is_error_result(result) and result["error_code"] == "insufficient_classes"


def test_inference_predicts_and_records_an_independent_evaluation(fake_domain_api, ctx, workspace_dir):
    api = FakeApplicationApi(fake_domain_api)
    reply = _ok(call_tool(train_classifier, _train_args(), ctx))
    new_rows = workspace_dir / "new_rows.csv"
    _frame("classification", 15, seed=9).to_csv(new_rows, index=False)

    result = _ok(call_tool(run_classifier_inference, {"params": {
        "input_path": str(new_rows), "model_path": reply["final_model_path"], "ground_truth_column": "target",
    }}, ctx))
    assert result["num_predictions"] == 15 and result["has_ground_truth"] is True
    assert {"accuracy", "auc", "precision", "recall", "specificity", "confusion_matrix"} <= set(result["metrics"])
    assert result["recorded_evaluation"]["variant_key"].startswith("blended_model__independent_")
    predictions = pd.read_csv(io.BytesIO(api.files[result["predictions_artifact_id"]]))
    assert {"prediction_label", "prediction_score_0", "prediction_score_1"} <= set(predictions.columns)

    wrong = call_tool(run_regression_inference, {"params": {
        "input_path": str(new_rows), "model_path": reply["final_model_path"],
    }}, ctx)
    assert is_error_result(wrong) and wrong["error_code"] == "wrong_task"


def test_a_model_trained_with_pycaret_is_legacy_and_names_refit_model(fake_domain_api, ctx, workspace_dir):
    api = FakeApplicationApi(fake_domain_api)
    reply = _ok(call_tool(train_classifier, _train_args(), ctx))
    selected = next(row for row in api.results.values() if row["variant_key"] == "blended_model")
    selected["environment"] = {"python": "3.11.3", "packages": {"pycaret": "3.3.2"}}  # no engine
    new_rows = workspace_dir / "new_rows.csv"
    _frame("classification", 5, seed=9).to_csv(new_rows, index=False)

    result = call_tool(run_classifier_inference, {"params": {
        "input_path": str(new_rows), "model_path": reply["final_model_path"],
    }}, ctx)
    assert is_error_result(result) and result["error_code"] == "legacy_model_format"
    assert "refit_model" in result["error_message"]
    assert result["model_result_id"] == selected["model_result_id"]


def test_refit_rebuilds_a_lost_pipeline_as_a_new_variant(fake_domain_api, ctx):
    api = FakeApplicationApi(fake_domain_api)
    _ok(call_tool(train_classifier, _train_args(), ctx))
    tuned = next(row for row in api.results.values() if row["variant_key"] == "tuned_model_1")

    result = _ok(call_tool(refit_model, {"model_result_id": tuned["model_result_id"]}, ctx))
    assert result["variant_key"].startswith("tuned_model_1__refit_")
    assert result["agreement"]["compared"] and result["agreement"]["identical_predictions"], result["agreement"]
    recorded = api.puts[-1]
    assert recorded["environment"]["refit_of"] == tuned["model_result_id"]
    assert recorded["environment"]["engine"] == "sklearn"
    assert persistence.load_bundle(result["model_path"] + ".pkl")["engine"] == "sklearn"
    assert not list(Path(result["model_path"]).parent.glob(".refit_*")), "the pending file was renamed"


def test_refit_maps_a_pycaret_record_onto_the_engine(fake_domain_api, ctx):
    from agent_system.toolpacks.radiomics.digests import bytes_digest

    api = FakeApplicationApi(fake_domain_api)
    legacy_id = str(uuid.uuid4())
    api.results[legacy_id] = {
        "model_result_id": legacy_id,
        "comparison_key": "classification:top_4:0123456789ab",
        "variant_key": "tuned_model_1",
        "algorithm": "LogisticRegression",
        "cohort_id": COHORT,
        "feature_set_id": FEATURE_SET,
        "input_file_id": None,
        "dataset_digest": bytes_digest(api.export["all"].encode("utf-8")),
        "parameters": {"estimator": "sklearn.linear_model._logistic.LogisticRegression",
                       "params": {"C": 1.0, "max_iter": 1000, "random_state": 11}},
        "preprocessing": {"setup": {"target": "target", "session_id": 11, "fold": 3, "normalize": True,
                                    "fix_imbalance": True, "log_experiment": False},
                          "requested_fix_imbalance": True, "effective_fix_imbalance": True},
        "feature_order": {"features": ["f1", "f2", "f3", "f4"], "target": "target"},
        "split_definition": {"kind": "selection_holdout", "fold": 3},
        "seeds": {"session_id": 11},
        "environment": {"packages": {"pycaret": "3.3.2"}},
        "metrics": {"stage": "tuned", "evaluation": {"kind": "selection_holdout", "generalisation_estimate": True}},
        "predictions_file_id": None,
    }
    result = _ok(call_tool(refit_model, {"model_result_id": legacy_id}, ctx))
    recorded = api.puts[-1]
    assert recorded["environment"]["refit_of"] == legacy_id
    assert recorded["preprocessing"]["mapped_from_pycaret_setup"]["normalize"] is True
    assert recorded["preprocessing"]["pipeline"]["numeric_steps"] == ["impute_median", "standard_scale"]
    assert recorded["preprocessing"]["class_balance"]["effective"] is True
    assert result["agreement"]["compared"] is False  # nothing recorded to compare with


def test_a_pycaret_class_path_cannot_be_refitted(fake_domain_api, ctx):
    api = FakeApplicationApi(fake_domain_api)
    legacy_id = str(uuid.uuid4())
    api.results[legacy_id] = {
        "model_result_id": legacy_id, "comparison_key": "classification:x:y", "variant_key": "v",
        "parameters": {"estimator": "pycaret.internal.pipeline.Pipeline", "params": {}},
        "seeds": {"session_id": 1}, "split_definition": {"kind": "random_split"},
    }
    result = call_tool(refit_model, {"model_result_id": legacy_id}, ctx)
    assert is_error_result(result) and result["error_code"] == "not_refittable"
    assert "PyCaret" in result["error_message"]


def test_train_regressor_on_a_generic_csv_and_predict(fake_domain_api, ctx, workspace_dir):
    api = FakeApplicationApi(fake_domain_api, task="regression")
    train_csv = workspace_dir / "rows.csv"
    _frame("regression", 40, seed=4).to_csv(train_csv, index=False)

    reply = _ok(call_tool(train_regressor, {"params": {
        "input_path": str(train_csv), "fold": 3, "n_select": 2, "include_models": "lr,ridge,dt",
    }}, ctx))
    assert reply["selection_holdout_used"] is False and reply["selected_variant"] == "blended_model"
    payload = api.puts[0]
    assert payload["split_definition"]["kind"] == "random_split"
    assert payload["split_definition"]["train_size"] == 0.8
    assert payload["preprocessing"]["pipeline"]["numeric_steps"] == ["impute_median", "yeo_johnson"]
    compared = next(v for v in payload["variants"] if v["variant_key"] == "compare_lr")
    assert set(compared["metrics"]["cv"]) == {"MAE", "MSE", "RMSE", "R2", "RMSLE", "MAPE"}
    assert compared["parameters"]["model_id"] == "lr"

    new_rows = workspace_dir / "new.csv"
    _frame("regression", 10, seed=8).to_csv(new_rows, index=False)
    result = _ok(call_tool(run_regression_inference, {"params": {
        "input_path": str(new_rows), "model_path": reply["final_model_path"], "ground_truth_column": "target",
    }}, ctx))
    assert result["num_predictions"] == 10
    assert {"mse", "rmse", "mae", "r2"} <= set(result["metrics"])
    assert result["metrics"]["r2"] > 0.8
