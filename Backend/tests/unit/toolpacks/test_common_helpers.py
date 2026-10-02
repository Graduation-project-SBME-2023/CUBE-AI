"""toolpacks/common: settings, service URLs, the shared client, the D24 write
headers, identity from the context and WorkspacePath (merged plan, Phase 2.2)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import BaseModel

from agent_system.toolkit.context import use_context
from agent_system.toolpacks.common import (
    domain_api,
    http_client,
    service_urls,
    settings,
    workspace,
)
from storage_paths import workspace as session_workspace
from tests.support.toolkit_fixtures import DOMAIN_API_URL, make_context

pytestmark = pytest.mark.unit

ATTEMPT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
RUN = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


# --- settings


def test_a_setting_is_read_when_used_not_at_import(monkeypatch):
    monkeypatch.delenv("DOMAIN_API_TIMEOUT_SECONDS", raising=False)
    assert settings.domain_api_timeout() == settings.DEFAULT_DOMAIN_API_TIMEOUT_S
    monkeypatch.setenv("DOMAIN_API_TIMEOUT_SECONDS", "12.5")
    assert settings.domain_api_timeout() == 12.5


@pytest.mark.parametrize("raw", ["abc", "0", "-3", "nan-ish"])
def test_an_invalid_setting_falls_back_and_says_why(monkeypatch, caplog, raw):
    monkeypatch.setenv("TOOL_HTTP_CONNECT_SECONDS", raw)
    with caplog.at_level(logging.WARNING, logger=settings.__name__):
        assert settings.connect_timeout() == settings.DEFAULT_CONNECT_TIMEOUT_S
    assert "TOOL_HTTP_CONNECT_SECONDS" in caplog.text


def test_the_long_timeout_is_never_below_two_minutes(monkeypatch):
    monkeypatch.setenv("DOMAIN_API_TIMEOUT_SECONDS", "10")
    assert settings.domain_api_long_timeout() == settings.MIN_LONG_TIMEOUT_S
    monkeypatch.setenv("DOMAIN_API_TIMEOUT_SECONDS", "300")
    assert settings.domain_api_long_timeout() == 300


# --- service URLs


def test_the_inference_url_reads_its_old_name_too(monkeypatch):
    monkeypatch.delenv("INFERENCE_API_URL", raising=False)
    monkeypatch.setenv("MONAI_URL", "http://monailabel:8000/")
    assert service_urls.inference_api_url() == "http://monailabel:8000"
    monkeypatch.setenv("INFERENCE_API_URL", "http://inference:8000")
    assert service_urls.inference_api_url() == "http://inference:8000"


@pytest.mark.parametrize("raw", ["application-api:8001", "ftp://x", "http://"])
def test_a_malformed_base_url_is_refused(monkeypatch, raw):
    monkeypatch.setenv("APP_API_URL", raw)
    with pytest.raises(ValueError, match="APP_API_URL"):
        service_urls.app_api_url()


# --- the shared client


def test_one_client_per_loop_and_explicit_timeouts():
    async def two_calls() -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
        return http_client.shared_client(), http_client.shared_client()

    first, again = asyncio.run(two_calls())
    other_loop, _ = asyncio.run(two_calls())

    assert first is again
    assert other_loop is not first  # a client is bound to the loop that made it
    assert first.timeout.connect == settings.connect_timeout()
    assert first.timeout.read == settings.domain_api_timeout()
    assert first.timeout.pool == settings.pool_timeout()


def test_a_closed_client_is_replaced():
    async def main() -> bool:
        first = http_client.shared_client()
        await http_client.aclose_shared_client()
        return http_client.shared_client() is not first

    assert asyncio.run(main())


# --- D24 write-path headers


def test_an_attempt_declares_the_attempt_path():
    with use_context(make_context(run_id=RUN, attempt_id=ATTEMPT, job_id="j1")):
        assert http_client.write_path_headers() == {"X-Attempt-Id": ATTEMPT}


def test_an_internal_job_without_an_attempt_declares_the_run_path():
    with use_context(make_context(run_id=RUN, job_id="j1")):
        assert http_client.write_path_headers() == {"X-Run-Id": RUN}


def test_a_run_alone_is_not_a_job():
    with use_context(make_context(run_id=RUN)):
        assert http_client.write_path_headers() == {}


def test_no_context_declares_no_path():
    assert http_client.write_path_headers() == {}


# --- domain_api: identity and requests


def test_identity_comes_from_the_context(monkeypatch):
    monkeypatch.setenv("SESSION_ID", "from-env")  # ignored: identity is never an env read
    monkeypatch.setenv("RUN_ID", "from-env")
    with use_context(make_context(session_id="s1", run_id=RUN, attempt_id=ATTEMPT)):
        assert domain_api.session_id() == "s1"
        assert domain_api.run_id() == RUN
        assert domain_api.attempt_id() == ATTEMPT
        assert domain_api.require_run() == RUN
        assert domain_api.require_session() == "s1"


def test_without_a_context_there_is_no_identity():
    assert domain_api.session_id() == domain_api.run_id() == domain_api.attempt_id() == ""
    with pytest.raises(domain_api.DomainApiError, match="no session"):
        domain_api.require_session()
    with pytest.raises(domain_api.DomainApiError, match="no run"):
        domain_api.require_run()


def _request_under_attempt(method: str) -> httpx.Request:
    async def call() -> None:
        with use_context(make_context(run_id=RUN, attempt_id=ATTEMPT)):
            await domain_api.request(method, "/things/1", json={"a": 1} if method != "GET" else None)

    with respx.mock(base_url=DOMAIN_API_URL) as router:
        route = router.route(path="/things/1").mock(return_value=httpx.Response(200, json={}))
        asyncio.run(call())
    return route.calls.last.request


def test_a_write_carries_the_attempt_header(monkeypatch):
    monkeypatch.setenv("APP_API_URL", DOMAIN_API_URL)
    for method in ("POST", "PUT", "PATCH", "delete"):
        assert _request_under_attempt(method).headers["X-Attempt-Id"] == ATTEMPT, method


def test_a_read_carries_no_write_path(monkeypatch):
    monkeypatch.setenv("APP_API_URL", DOMAIN_API_URL)
    assert "X-Attempt-Id" not in _request_under_attempt("GET").headers


def test_a_request_uses_the_per_call_timeout(monkeypatch):
    monkeypatch.setenv("APP_API_URL", DOMAIN_API_URL)

    async def call() -> None:
        with use_context(make_context()):
            await domain_api.request("GET", "/slow", timeout=99.0)

    with respx.mock(base_url=DOMAIN_API_URL) as router:
        route = router.get("/slow").mock(return_value=httpx.Response(200, json={}))
        asyncio.run(call())
    sent = route.calls.last.request.extensions["timeout"]
    assert sent["read"] == 99.0 and sent["connect"] == settings.connect_timeout()


# --- WorkspacePath


class _Args(BaseModel):
    path: workspace.WorkspacePath = None


def test_a_workspace_path_resolves_against_the_contexts_workspace(tmp_path):
    with use_context(make_context(workspace_dir=tmp_path)):
        resolved = _Args(path="reports/final.pdf").path
    assert Path(resolved) == tmp_path / "outputs" / "reports" / "final.pdf"


def test_without_a_workspace_dir_the_sessions_workspace_is_used():
    with use_context(make_context(session_id="s-42")):
        assert workspace.workspace_dir() == session_workspace("s-42")


def test_two_sessions_never_share_a_workspace(tmp_path):
    with use_context(make_context(session_id="a", workspace_dir=tmp_path / "a")):
        a = _Args(path="x.csv").path
    with use_context(make_context(session_id="b", workspace_dir=tmp_path / "b")):
        b = _Args(path="x.csv").path
    assert Path(a).is_relative_to(tmp_path / "a")
    assert Path(b).is_relative_to(tmp_path / "b")


def test_without_a_context_a_workspace_path_is_invalid():
    with pytest.raises(ValueError, match="no tool context"):
        _Args(path="x.csv")
