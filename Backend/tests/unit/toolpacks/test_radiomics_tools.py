"""The two radiomics tools end to end, against a mocked Application API.

Synthetic NIfTI volumes and masks only. PyRadiomics is not installed in the
test environment: ``fake_extractor`` replaces the extraction worker with an
in-process function that measures the mask with SimpleITK.
"""

from __future__ import annotations

import json
import sys
import types
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
sitk = pytest.importorskip("SimpleITK")
pytest.importorskip("sklearn")

from agent_system.toolkit.results import is_error_result  # noqa: E402
from agent_system.toolpacks.radiomics.extraction import worker  # noqa: E402
from agent_system.toolpacks.radiomics.analyze_importance import radiomics_analyze_importance  # noqa: E402
from agent_system.toolpacks.radiomics.extract_features import radiomics_extract_features  # noqa: E402
from storage_paths import workspace_subpath  # noqa: E402
from tests.support.toolkit_fixtures import TEST_SESSION_ID, call_tool, make_context  # noqa: E402

pytestmark = pytest.mark.unit

RUN = "22222222-2222-4222-8222-222222222222"
ATTEMPT = "33333333-3333-4333-8333-333333333333"
COHORT = "55555555-5555-4555-8555-555555555555"
N_VOLUMES = 16


def _volume(index: int) -> str:
    return f"1.2.840.{index}"


# ----------------------------------------------------------------------
# Synthetic data
# ----------------------------------------------------------------------
def _write_pair(workspace: Path, index: int, rng) -> None:
    """A 24³ CT-like volume and a mask whose lesion size follows the label."""
    image = rng.normal(40, 20, size=(24, 24, 24)).astype(np.float32)
    mask = np.zeros_like(image, dtype=np.uint8)
    half = 3 + (index % 2) * 3 + index % 3  # positives get the larger lesions
    mask[12 - half:12 + half, 12 - half:12 + half, 12 - half:12 + half] = 1
    image[mask == 1] += 60 + 10 * (index % 2)
    image[0, 0, 0] = -1000  # an air voxel: CT-like intensity range
    for array, folder in ((image, "volumes"), (mask, "masks")):
        sitk.WriteImage(sitk.GetImageFromArray(array), str(_path(workspace, folder, _volume(index))))


def _path(workspace: Path, folder: str, uid: str) -> Path:
    """Where the workspace keeps a volume or mask file (the tools resolve the same way)."""
    path = workspace_subpath(workspace, f"{folder}/{uid}.nii.gz")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _analysis_rows() -> list[dict[str, Any]]:
    return [
        {"volume_series_instance_uid": _volume(i), "subject_key": f"p{i}", "target": i % 2, "ordinal": i}
        for i in range(N_VOLUMES)
    ]


def _reviewed_pairs(indices) -> list[dict[str, Any]]:
    return [
        {
            "volume_series_instance_uid": _volume(i),
            "target_key": "kidney",
            "pair_key": f"pair-{i}",
            "pair_id": str(uuid.uuid4()),
            "selected_segmentation_uid": f"2.25.{i}",
            "segment_number": 1,
            "execution_checkpoint": {"mask_path": f"masks/{_volume(i)}.nii.gz"},
        }
        for i in indices
    ]


class FakeApi:
    """The routes the radiomics tools use, with what they wrote."""

    def __init__(self, router) -> None:
        self.batches: list[list[dict]] = []
        self.rows: list[dict] = []
        self.feature_sets: dict[str, dict] = {}
        self.files: list[str] = []
        self.pairs = _reviewed_pairs(range(N_VOLUMES))
        router.get(f"/cohorts/session/{TEST_SESSION_ID}/population").respond(
            json={"cohort_id": COHORT, "frozen_at": "2026-10-06T00:00:00Z"}
        )
        router.get(f"/cohorts/{COHORT}/analysis-rows").respond(json=_analysis_rows())
        router.get(f"/segmentation-batches/session/{TEST_SESSION_ID}/reviewed-masks").mock(
            side_effect=lambda request: httpx.Response(200, json=self.pairs)
        )
        router.post(f"/radiomics/run/{RUN}/batch").mock(side_effect=self._record)
        router.get(f"/radiomics/session/{TEST_SESSION_ID}").mock(
            side_effect=lambda request: httpx.Response(200, json=self.rows)
        )
        router.put(url__regex=r"/feature-sets/run/[^/]+/[^/]+$").mock(side_effect=self._feature_set)
        router.post(url__regex=r"/feature-sets/[^/]+/freeze$").mock(side_effect=self._freeze)
        router.post("/files/").mock(side_effect=self._file)

    def _record(self, request: httpx.Request) -> httpx.Response:
        rows = json.loads(request.content)["rows"]
        self.batches.append(rows)
        stored = [{**row, "radiomics_feature_id": str(uuid.uuid4()), "target_key": "kidney"} for row in rows]
        self.rows.extend(stored)
        return httpx.Response(200, json={"created": len(rows), "reused": 0, "rows": stored})

    def _feature_set(self, request: httpx.Request) -> httpx.Response:
        set_key = request.url.path.rsplit("/", 1)[-1]
        feature_set_id = str(uuid.uuid4())
        self.feature_sets[set_key] = {**json.loads(request.content), "feature_set_id": feature_set_id}
        return httpx.Response(200, json={"feature_set_id": feature_set_id})

    def _freeze(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"feature_set_id": request.url.path.split("/")[2], "frozen_at": "t"})

    def _file(self, request: httpx.Request) -> httpx.Response:
        name = request.content.split(b'name="display_filename"\r\n\r\n')[1].split(b"\r\n")[0].decode()
        self.files.append(name)
        return httpx.Response(200, json={"file_id": str(uuid.uuid4()), "logical_key": name, "display_filename": name})


def fake_extract(image_path: str, mask_path: str, params_file: str) -> dict[str, float]:
    """What PyRadiomics would measure, enough to rank features."""
    image = sitk.GetArrayFromImage(sitk.ReadImage(image_path))
    mask = sitk.GetArrayFromImage(sitk.ReadImage(mask_path)) > 0
    inside = image[mask]
    return {
        "original_shape_VoxelVolume": float(mask.sum()),
        "original_firstorder_Mean": float(inside.mean()),
        "original_firstorder_Maximum": float(inside.max()),
        "original_firstorder_Variance": float(inside.var()),
        "original_glcm_Contrast": float(np.nan),  # a non-finite value is recorded as null
    }


class FakePool:
    """The PyRadiomics worker, in-process."""

    async def check_settings(self, params_file):
        assert params_file.endswith("radiomics_params.yaml")

    async def extract(self, image_path, mask_path, params_file, timeout):
        return fake_extract(image_path, mask_path, params_file)

    async def close(self):
        pass


@pytest.fixture
def fake_extractor(monkeypatch):
    """PyRadiomics, replaced: the version the configuration digest names,
    and the extraction worker run in-process."""
    radiomics = types.ModuleType("radiomics")
    radiomics.__version__ = "3.1.0-test"
    monkeypatch.setitem(sys.modules, "radiomics", radiomics)
    monkeypatch.setattr(worker, "ExtractorPool", FakePool)


@pytest.fixture
def api(fake_domain_api):
    return FakeApi(fake_domain_api)


@pytest.fixture
def workspace(workspace_dir):
    rng = np.random.default_rng(0)
    for index in range(N_VOLUMES):
        _write_pair(workspace_dir, index, rng)
    return workspace_dir


@pytest.fixture
def ctx(workspace):
    return make_context(
        run_id=RUN, attempt_id=ATTEMPT, workspace_dir=workspace, agent_type="feature_extraction_agent"
    )


def _ok(result) -> dict[str, Any]:
    assert not is_error_result(result), result
    return json.loads(result)


# ----------------------------------------------------------------------
# radiomics_extract_features
# ----------------------------------------------------------------------
def test_extraction_records_one_row_per_reviewed_mask(api, ctx, fake_extractor):
    result = _ok(call_tool(radiomics_extract_features, {"params": {}}, ctx))

    assert result["status"] == "success"
    assert result["radiomics_rows"] == {"created": N_VOLUMES, "reused": 0, "total": N_VOLUMES}
    assert result["num_subjects"] == N_VOLUMES and result["mask_labels"] == [1]
    assert result["exports"] == {"radiomics_features": f"/radiomics/run/{RUN}/export"}
    assert result["num_features"] == {"kidney": 5}  # the measured features; presence is not one
    assert result["summary"]["extraction_params_used"]["image_types"] == ["Original", "Wavelet"]
    assert set(result["summary"]) >= {"total_patients", "volume_range", "gpu_available"}

    row = api.batches[0][0]
    assert row["features"]["original_glcm_Contrast"] is None
    assert row["extractor_version"] == "3.1.0-test"
    for digest in ("config_digest", "image_digest", "segmentation_digest", "source_digest"):
        assert len(row[digest]) == 64, digest
    # This synthetic range (-1000 to ~200) is "MRI-like" for the adaptive rule: range / 128.
    assert row["configuration"]["pyradiomics"]["setting"]["binWidth"] == 9.28


def test_extraction_digests_are_stable_across_replays(api, ctx, fake_extractor):
    call_tool(radiomics_extract_features, {"params": {}}, ctx)
    call_tool(radiomics_extract_features, {"params": {}}, ctx)
    first, second = api.batches[0], api.batches[1]
    assert [r["source_digest"] for r in first] == [r["source_digest"] for r in second]


def test_the_content_addressed_identity_is_unchanged(api, ctx, fake_extractor):
    """Golden: the digests the extraction produced for this data before its
    refactor, so rows recorded earlier keep being reused, never duplicated."""
    _ok(call_tool(radiomics_extract_features, {"params": {}}, ctx))
    rows = api.batches[0]
    assert rows[0]["config_digest"] == "9bc544617a2bc86d30153bc20c2ca62d76e28a2f06e7aa05358c780d00c7a4ec"
    assert [row["source_digest"] for row in rows[:2]] == [
        "9d81c7aae574186749674ae17222383b171bd775e7d6cb17b55e8ff0566900db",
        "70e1983b192f4389a26327e1eab2e4ade12f5dd20539c4e805dfdb503ed0cfa3",
    ]


def test_the_bin_width_adapts_to_the_intensity_range(tmp_path):
    nibabel = pytest.importorskip("nibabel")
    from agent_system.toolpacks.radiomics.extraction.config import adaptive_bin_width

    def volume(name, low, high):
        data = np.linspace(low, high, 8 * 8 * 8, dtype=np.float32).reshape(8, 8, 8)
        path = tmp_path / f"{name}.nii.gz"
        nibabel.save(nibabel.Nifti1Image(data, np.eye(4)), path)
        return str(path)

    assert adaptive_bin_width([volume("ct", -1024, 3071)], 25.0) == 25.0  # Hounsfield units
    assert adaptive_bin_width([volume("mri", 0, 1280)], 25.0) == 10.0  # 128 bins
    assert adaptive_bin_width([str(tmp_path / "missing.nii.gz")], 25.0) == 25.0


def test_feature_classes_given_as_image_types_are_moved(api, ctx, fake_extractor):
    params = {"feature_classes": "firstorder,wavelet", "image_types": "original"}
    _ok(call_tool(radiomics_extract_features, {"params": params}, ctx))
    configuration = api.batches[0][0]["configuration"]["pyradiomics"]
    assert set(configuration["imageType"]) == {"Original", "Wavelet"}
    assert set(configuration["featureClass"]) == {"firstorder"}


def test_a_pair_without_a_label_is_skipped_and_reported(api, ctx, fake_extractor):
    api.pairs = _reviewed_pairs(range(N_VOLUMES)) + [{
        **_reviewed_pairs([0])[0], "volume_series_instance_uid": "9.9.9", "pair_key": "stray",
    }]
    # The stray pair's image exists under its own uid.
    image = sitk.ReadImage(str(_path(ctx.workspace_dir, "volumes", _volume(0))))
    sitk.WriteImage(image, str(_path(ctx.workspace_dir, "volumes", "9.9.9")))

    result = _ok(call_tool(radiomics_extract_features, {"params": {}}, ctx))

    assert result["unlabeled_subject_ids"] == ["9.9.9"]
    assert result["radiomics_rows"]["created"] == N_VOLUMES


def test_a_too_small_mask_is_not_measured(api, ctx, fake_extractor):
    tiny = np.zeros((24, 24, 24), dtype=np.uint8)
    tiny[1, 1, 1:4] = 1
    sitk.WriteImage(sitk.GetImageFromArray(tiny), str(_path(ctx.workspace_dir, "masks", _volume(3))))

    result = _ok(call_tool(radiomics_extract_features, {"params": {}}, ctx))

    assert result["radiomics_rows"]["created"] == N_VOLUMES - 1
    assert _volume(3) in result["summary"]["patients_with_spatial_warnings"]


def test_no_reviewed_mask_is_an_error(api, ctx, fake_extractor):
    api.pairs = []
    result = call_tool(radiomics_extract_features, {"params": {}}, ctx)
    assert is_error_result(result) and result["error_code"] == "no_reviewed_masks"


def test_an_unapproved_cohort_is_an_error(api, ctx, fake_extractor, fake_domain_api):
    fake_domain_api.get(f"/cohorts/session/{TEST_SESSION_ID}/population").respond(json={"cohort_id": COHORT})
    result = call_tool(radiomics_extract_features, {"params": {}}, ctx)
    assert is_error_result(result) and result["error_code"] == "cohort_not_approved"


def test_a_failed_batch_write_is_an_error(api, ctx, fake_extractor, fake_domain_api):
    fake_domain_api.post(f"/radiomics/run/{RUN}/batch").mock(return_value=httpx.Response(503))
    result = call_tool(radiomics_extract_features, {"params": {}}, ctx)
    assert is_error_result(result) and result["error_code"] == "upstream_error"


# ----------------------------------------------------------------------
# radiomics_analyze_importance
# ----------------------------------------------------------------------
@pytest.fixture
def extracted(api, ctx, fake_extractor):
    _ok(call_tool(radiomics_extract_features, {"params": {}}, ctx))
    return api


@pytest.mark.parametrize("method", ["rf", "f_test", "mutual_info", "rfe"])
def test_selection_freezes_a_set_per_top_n_and_all_features(extracted, ctx, method):
    params = {"top_features": "2,4", "method": method}
    result = _ok(call_tool(radiomics_analyze_importance, {"params": params}, ctx))

    assert [item["set_key"] for item in result["feature_sets"]] == ["top_2", "top_4", "all_features"]
    assert result["method"] == method and result["task_type"] == "classification"
    assert result["label_supervised_on_full_data"] is False
    chart = result["chart_data"]
    assert set(chart) >= {"importance_bars", "cumulative", "correlation", "pca", "tsne"}
    assert len(chart["importance_bars"]) == result["feature_count"]
    assert chart["pca"]["points"] and chart["tsne"]["points"]

    top_2 = extracted.feature_sets["top_2"]
    assert len(top_2["feature_names"]) == 2
    assert len(top_2["feature_matrix"]) == N_VOLUMES
    assert top_2["selection"]["holdout_groups"]  # selection never saw the holdout labels
    assert top_2["extraction_config"]["selection_method"] == method


def test_selection_publishes_its_figures(extracted, ctx):
    result = _ok(call_tool(radiomics_analyze_importance, {"params": {}}, ctx))
    names = {figure["display_filename"] for figure in result["figures"]}
    assert names == {
        "feature_importance_plot.png", "cumulative_importance_plot.png", "feature_correlation_heatmap.png",
    }


def test_no_holdout_is_recorded_as_label_supervised(extracted, ctx):
    result = _ok(call_tool(radiomics_analyze_importance, {"params": {"holdout_size": 0}}, ctx))
    assert result["label_supervised_on_full_data"] is True


def test_selection_without_recorded_rows_is_an_error(api, ctx):
    result = call_tool(radiomics_analyze_importance, {"params": {}}, ctx)
    assert is_error_result(result) and result["error_code"] == "no_radiomics_rows"


def test_an_unknown_method_falls_back_to_random_forest(extracted, ctx):
    result = _ok(call_tool(radiomics_analyze_importance, {"params": {"method": "magic"}}, ctx))
    assert result["method"] == "rf"
