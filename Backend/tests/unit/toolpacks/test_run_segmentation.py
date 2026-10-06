"""``run_segmentation`` against a mocked MONAI Label: the job, its multipart
result, the datastore upload, and every failure as an ``error_result``."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from agent_system.toolkit.results import is_error_result
from agent_system.toolpacks.segmentation import monai_client
from agent_system.toolpacks.segmentation.run_segmentation import run_segmentation
from tests.support.toolkit_fixtures import call_tool, make_context

pytestmark = pytest.mark.unit

INFERENCE = "http://inference.test"
SERIES = "1.2.3"
BOUNDARY = "cubeai-boundary"


def _multipart(params: dict, mask: bytes) -> httpx.Response:
    body = (
        f"--{BOUNDARY}\r\nContent-Type: application/json\r\n\r\n{json.dumps(params)}\r\n"
        f"--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"mask.nii.gz\"\r\n"
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + mask + f"\r\n--{BOUNDARY}--\r\n".encode()
    return httpx.Response(200, content=body, headers={"content-type": f"multipart/form-data; boundary={BOUNDARY}"})


@pytest.fixture
def monai(monkeypatch):
    monkeypatch.setenv("INFERENCE_API_URL", INFERENCE)
    monkeypatch.setenv("SEGMENTATION_POLL_SECONDS", "0.01")
    with respx.mock(base_url=INFERENCE, assert_all_called=False) as router:
        yield router


def _job_url(params: dict) -> str:
    return f"/inference-jobs/{monai_client.inference_job_id('spleen_model', SERIES, params)}"


def _call(**args):
    arguments = {"model": "spleen_model", "series_instance_uid": SERIES, "label_info": "spleen", **args}
    return call_tool(run_segmentation, arguments, make_context())


def _sent_params() -> dict:
    return {"label_info": [{"name": "spleen", "idx": 1, "color": [255, 0, 0],
                            "description": "Spleen segmentation", "model_name": "spleen_model"}]}


def test_a_segmentation_is_inferred_and_saved_without_a_temp_file(monai, tmp_path, monkeypatch):
    job = _job_url(_sent_params())
    monai.post("/inference-jobs/").respond(json={"job_id": "j"})
    monai.get(job).mock(side_effect=[
        httpx.Response(200, json={"status": "RUNNING"}),
        httpx.Response(200, json={"status": "SUCCEEDED", "job_id": "j", "attempt": 1}),
    ])
    monai.get(f"{job}/result").mock(return_value=_multipart({"latencies": {"total": 1}}, b"NIFTI"))
    upload = monai.put("/datastore/label").respond(text="label-7\n")

    result = _call()

    assert not is_error_result(result), result
    assert (result["saved"], result["label_id"], result["error"]) == (True, "label-7", False)
    assert result["segmentation_bytes_count"] == 5 and result["original_filename"] == "mask.nii.gz"
    sent = upload.calls.last.request
    assert b"NIFTI" in sent.content and b"latencies" in sent.content
    assert sent.url.params["tag"] == "final"


def test_a_failed_job_is_an_error_result(monai):
    job = _job_url(_sent_params())
    monai.post("/inference-jobs/").respond(json={})
    monai.get(job).respond(json={"status": "FAILED", "error": "out of memory"})

    result = _call()

    assert is_error_result(result)
    assert result["error_code"] == "inference_failed" and "out of memory" in result["error_message"]


def test_a_mask_that_was_not_saved_fails_the_call(monai):
    job = _job_url(_sent_params())
    monai.post("/inference-jobs/").respond(json={})
    monai.get(job).respond(json={"status": "SUCCEEDED"})
    monai.get(f"{job}/result").mock(return_value=_multipart({}, b"NIFTI"))
    monai.put("/datastore/label").mock(return_value=httpx.Response(500, text="Traceback: secret"))

    result = _call()

    assert result["error_code"] == "datastore_save_failed"
    assert "Traceback" not in result["error_message"]  # a 5xx body never reaches the model


def test_a_result_that_is_not_multipart_is_invalid(monai):
    job = _job_url(_sent_params())
    monai.post("/inference-jobs/").respond(json={})
    monai.get(job).respond(json={"status": "SUCCEEDED"})
    monai.get(f"{job}/result").respond(json={"oops": True})

    assert _call()["error_code"] == "invalid_response"


def test_no_organ_is_an_invalid_argument(monai):
    assert _call(label_info=" , ")["error_code"] == "invalid_args"
