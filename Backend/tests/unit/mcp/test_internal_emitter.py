"""Backend/mcp/internal_emitter.py -- the non-blocking, authenticated,
counted-failure POST every MCP tool subprocess uses to reach /events and
/ws-broadcast (T10, WEBSOCKET_FIX_PLAN.md WS-07/W11).

Backend/mcp/ is not one of conftest.py's import roots (it is its own
subprocess entry point, launched by Backend/mcp/server.py, which adds itself
to sys.path the same way any directly-run script does) -- so this test adds
it locally rather than touching the shared conftest.py.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

MCP_ROOT = Path(__file__).resolve().parents[3] / "mcp"
if str(MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(MCP_ROOT))

import internal_emitter  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_failure_count():
    internal_emitter.failure_count = 0
    yield
    internal_emitter.failure_count = 0


@pytest.mark.asyncio
async def test_post_internal_returns_promptly_and_counts_a_timeout(monkeypatch):
    async def slow_post(self, *args, **kwargs):
        raise httpx.TimeoutException("simulated server timeout")

    monkeypatch.setattr(httpx.AsyncClient, "post", slow_post)

    ok = await asyncio.wait_for(
        internal_emitter.post_internal("/events", {"a": 1}), timeout=1.0
    )

    assert ok is False
    assert internal_emitter.failure_count == 1


@pytest.mark.asyncio
async def test_post_internal_counts_every_failure(monkeypatch):
    async def failing_post(self, *args, **kwargs):
        raise httpx.ConnectError("simulated connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "post", failing_post)

    await internal_emitter.post_internal("/events", {})
    await internal_emitter.post_internal("/events", {})

    assert internal_emitter.failure_count == 2


@pytest.mark.asyncio
async def test_post_internal_sends_the_internal_token_header(monkeypatch):
    monkeypatch.setenv("WS_INTERNAL_TOKEN", "a-real-secret")
    captured = {}

    async def fake_post(self, url, json=None, headers=None, **kwargs):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    ok = await internal_emitter.post_internal("/events", {"a": 1})

    assert ok is True
    assert captured["headers"] == {"X-Internal-Token": "a-real-secret"}


@pytest.mark.asyncio
async def test_broadcast_ws_message_is_a_noop_without_a_session_id(monkeypatch):
    post = AsyncMock()
    monkeypatch.setattr(httpx.AsyncClient, "post", post)

    ok = await internal_emitter.broadcast_ws_message("population_output", {}, None)

    assert ok is False
    post.assert_not_called()


@pytest.mark.asyncio
async def test_fire_and_forget_returns_immediately_even_when_the_post_hangs():
    started = asyncio.Event()
    release = asyncio.Event()

    async def hanging_coro():
        started.set()
        await release.wait()

    internal_emitter.fire_and_forget(hanging_coro())

    await asyncio.wait_for(started.wait(), timeout=1.0)  # scheduled...
    release.set()  # ...but fire_and_forget() itself never blocked on it
