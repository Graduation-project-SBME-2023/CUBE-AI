"""Orthanc client tests driven through httpx's mock transport -- no network.

The behaviour that matters here is the part that used to be missing: sending
credentials, and turning an Orthanc rejection into a clear error instead of a
KeyError somewhere further down.
"""

from __future__ import annotations

import base64

import httpx
import pytest

from clients.orthanc_client import OrthancClient, OrthancError

BASE_URL = "http://orthanc.test:8042"


def _client(handler) -> OrthancClient:
    transport = httpx.MockTransport(handler)
    return OrthancClient(BASE_URL, client=httpx.AsyncClient(transport=transport))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_series_returns_the_first_orthanc_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/tools/find"
        return httpx.Response(200, json=["series-abc", "series-def"])

    async with _client(handler) as client:
        assert await client.find_series("1.2.3") == "series-abc"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_series_returns_none_when_absent():
    async with _client(lambda request: httpx.Response(200, json=[])) as client:
        assert await client.find_series("1.2.3") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_returns_the_new_instance_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["content-type"] == "application/dicom"
        assert request.content == b"dicom-bytes"
        return httpx.Response(200, json={"ID": "instance-1", "Status": "Success"})

    async with _client(handler) as client:
        assert await client.upload_dicom(b"dicom-bytes") == "instance-1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_rejection_raises_with_the_server_reason():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Not a DICOM file")

    async with _client(handler) as client:
        with pytest.raises(OrthancError, match="HTTP 400"):
            await client.upload_dicom(b"not-dicom")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_without_an_instance_id_is_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Status": "Success"})

    async with _client(handler) as client:
        with pytest.raises(OrthancError, match="no instance id"):
            await client.upload_dicom(b"dicom-bytes")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reading_a_missing_series_is_an_error_not_an_empty_list():
    async with _client(lambda request: httpx.Response(200, json=[])) as client:
        with pytest.raises(OrthancError, match="was not found"):
            await client.read_series_datasets("1.2.3")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_credentials_are_sent_when_configured(monkeypatch):
    # The previous agent-side calls sent no credentials at all, which only works
    # while Orthanc runs with authentication disabled.
    monkeypatch.setenv("ORTHANC_USER", "orthanc")
    monkeypatch.setenv("ORTHANC_PASSWORD", "secret")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json=[])

    client = OrthancClient(BASE_URL)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        auth=(
            "orthanc",
            "secret",
        ),
    )
    async with client:
        await client.find_series("1.2.3")

    expected = base64.b64encode(b"orthanc:secret").decode()
    assert seen["authorization"] == f"Basic {expected}"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_credentials_are_sent_when_unset(monkeypatch):
    monkeypatch.delenv("ORTHANC_USER", raising=False)
    monkeypatch.delenv("ORTHANC_PASSWORD", raising=False)
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json=[])

    async with _client(handler) as client:
        await client.find_series("1.2.3")

    assert seen["authorization"] is None


@pytest.mark.unit
def test_base_url_trailing_slash_is_normalised():
    client = OrthancClient("http://orthanc.test:8042/")
    assert client.base_url == "http://orthanc.test:8042"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_series_treats_a_missing_series_as_success():
    # Rollback of a partial upload must not fail because the series is already
    # gone.
    async with _client(lambda request: httpx.Response(404)) as client:
        assert await client.delete_series("gone") is True


# ----------------------------------------------------------------------
# Retry: a long slice-upload loop must survive one dropped connection.
#
# A real volume is hundreds of sequential requests, and Orthanc closing one
# idle keep-alive connection -- or being briefly saturated by several
# concurrent uploads -- used to fail the entire job on the very first
# transient hiccup, which is exactly what happened uploading real files.
# ----------------------------------------------------------------------


def _flaky_handler(*, fail_times: int, ok_response: httpx.Response):
    """A handler that raises a transport error the first ``fail_times`` calls."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] <= fail_times:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response")
        return ok_response

    handler.calls = calls
    return handler


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_survives_one_dropped_connection():
    handler = _flaky_handler(
        fail_times=1, ok_response=httpx.Response(200, json={"ID": "instance-1"})
    )

    async with _client(handler) as client:
        assert await client.upload_dicom(b"dicom-bytes") == "instance-1"
    assert handler.calls["count"] == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_gives_up_after_repeated_drops():
    handler = _flaky_handler(
        fail_times=10, ok_response=httpx.Response(200, json={"ID": "instance-1"})
    )

    async with _client(handler) as client:
        with pytest.raises(httpx.RemoteProtocolError):
            await client.upload_dicom(b"dicom-bytes")
    # Bounded retries, not an unbounded loop against a genuinely dead server.
    assert handler.calls["count"] == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_series_survives_a_read_error():
    handler = _flaky_handler(fail_times=2, ok_response=httpx.Response(200, json=["series-1"]))

    async with _client(handler) as client:
        assert await client.find_series("1.2.3") == "series-1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_client_rejection_is_not_retried():
    # A 4xx means Orthanc understood and refused the request; retrying an
    # identical request cannot change that, so the client must not delay the
    # caller pretending otherwise.
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(400, text="Not a DICOM file")

    async with _client(handler) as client:
        with pytest.raises(OrthancError):
            await client.upload_dicom(b"not-dicom")
    assert calls["count"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_series_retries_and_still_reports_failure_gracefully():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("connection reset")

    async with _client(handler) as client:
        # Best-effort cleanup: exhausting retries must return False, not raise,
        # so a failed rollback never masks the original ingestion error.
        assert await client.delete_series("some-series") is False
