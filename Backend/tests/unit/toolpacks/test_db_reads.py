"""The DB read tools (merged plan, Phase 2.4, L1-L4): the shared client, an
``error_result`` instead of an exception, pagination, the unvalidated filter
and one implementation of "the masks of a volume"."""

from __future__ import annotations

import httpx
import pytest

from agent_system.toolpacks.db_reads import _api
from agent_system.toolpacks.db_reads.get_patients_info import get_all_patients_info
from agent_system.toolpacks.db_reads.get_studies_info import get_all_studies_info
from agent_system.toolpacks.db_reads.models.get_model import get_model
from agent_system.toolpacks.db_reads.segmentations.get_segmentation import (
    get_invalidate_segmentations,
    get_segmentation_by_volume,
)
from agent_system.toolpacks.db_reads.volumes.get_volume import get_volume
from agent_system.toolpacks.segmentation.check_having_mask import check_having_mask
from tests.support.toolkit_fixtures import call_tool, make_context

pytestmark = pytest.mark.unit

MASK = {"label_uid": "m1", "inference_model_id": 7}


def _call(tool, args=None):
    return call_tool(tool, args or {}, make_context())


# --- L1: errors are results, never exceptions


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(404), "not_found"),
        (httpx.Response(422, text="bad uid"), "bad_request"),
        (httpx.Response(500, text="Traceback ... SELECT secret"), "upstream_error"),
        (httpx.Response(200, text="<html>"), "invalid_response"),
    ],
)
def test_a_failed_read_is_an_error_result(fake_domain_api, response, code):
    fake_domain_api.get("/volumes/v1").mock(return_value=response)

    result = _call(get_volume, {"volume_series_instance_uid": "v1"})

    assert result["status"] == "error"
    assert result["error_code"] == code
    assert "SELECT" not in result["error_message"]  # a 5xx body never reaches the model


def test_an_unreachable_api_is_an_error_result(fake_domain_api):
    fake_domain_api.get("/volumes/v1").mock(side_effect=httpx.ConnectError("refused"))

    result = _call(get_volume, {"volume_series_instance_uid": "v1"})

    assert result["error_code"] == "unavailable"


def test_a_successful_read_keeps_its_shape(fake_domain_api):
    fake_domain_api.get("/inference-models/7").respond(json={"inference_model_id": 7, "name": "x"})

    assert _call(get_model, {"model_id": 7}) == {"data": {"model_id": 7, "name": "x"}}


def test_reads_use_the_shared_client_with_explicit_timeouts(fake_domain_api, monkeypatch):
    seen = []
    real = _api.shared_client

    def spy():
        client = real()
        seen.append(client)
        return client

    monkeypatch.setattr(_api, "shared_client", spy)
    route = fake_domain_api.get("/volumes/v1").respond(json={})

    _call(get_volume, {"volume_series_instance_uid": "v1"})

    assert len(seen) == 1
    assert route.calls.last.request.extensions["timeout"]["connect"] is not None


# --- L2: the whole-table reads are paged


@pytest.mark.parametrize(("tool", "path"), [(get_all_patients_info, "/patients/"), (get_all_studies_info, "/studies/")])
def test_the_whole_table_reads_are_paged(fake_domain_api, tool, path):
    route = fake_domain_api.get(path).respond(json=[{"id": 1}])

    assert _call(tool, {"skip": 100, "limit": 50}) == [{"id": 1}]  # the raw list, as before
    assert route.calls.last.request.url.params == httpx.QueryParams({"skip": "100", "limit": "50"})


def test_a_page_is_at_most_one_hundred_rows(fake_domain_api):
    result = _call(get_all_patients_info, {"limit": 5000})
    assert result["error_code"] == "invalid_args"


# --- L3: unvalidated means unvalidated


def test_the_unvalidated_tool_asks_for_unvalidated_masks(fake_domain_api):
    route = fake_domain_api.get("/segmentation-masks/unvalidated/").respond(json=[MASK])

    assert _call(get_invalidate_segmentations) == {"data": [{"label_uid": "m1", "model_id": 7}]}
    assert route.called


# --- L4: "no mask" only when there is no mask


def test_a_volume_without_masks_has_none(fake_domain_api):
    fake_domain_api.get("/segmentation-masks/by-volume/v1").mock(return_value=httpx.Response(404))
    assert _call(check_having_mask, {"volume_uid": "v1"}) == []


def test_a_server_error_is_not_mistaken_for_no_mask(fake_domain_api):
    fake_domain_api.get("/segmentation-masks/by-volume/v1").mock(return_value=httpx.Response(500))
    result = _call(check_having_mask, {"volume_uid": "v1"})
    assert result["error_code"] == "upstream_error"


def test_the_two_mask_tools_agree(fake_domain_api):
    fake_domain_api.get("/segmentation-masks/by-volume/v1").respond(json=[MASK])

    masks = _call(check_having_mask, {"volume_uid": "v1"})

    assert masks == [{"label_uid": "m1", "model_id": 7}]
    assert _call(get_segmentation_by_volume, {"volume_uid": "v1"}) == {"data": masks}
