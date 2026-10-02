"""The tool packs' shared HTTP client and the D24 write-path headers
(merged plan, Phase 2.2).

``shared_client()`` returns one ``httpx.AsyncClient`` per running event loop,
so every tool call reuses the same connection pool instead of opening a client
per request. Its timeouts are explicit (``settings``); a request that needs a
longer one passes ``timeout=`` itself. A client is bound to the loop that
created it, which is why there is one per loop: the Agent System runs one
loop, the tests run many.

**D24:** the Application API fences every write on the execution path by the
path it declares (Appendix P). ``write_path_headers()`` turns the current
``ToolContext`` into that declaration: ``X-Attempt-Id`` for the running
attempt, or ``X-Run-Id`` for an internal job that has no attempt of its own.
Every write a tool sends to the Application API carries them.
"""

from __future__ import annotations

import asyncio
import weakref

import httpx

from .. import _toolkit
from . import settings

ATTEMPT_HEADER = "X-Attempt-Id"
RUN_HEADER = "X-Run-Id"

_clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient] = (
    weakref.WeakKeyDictionary()
)


def timeout(read: float | None = None) -> httpx.Timeout:
    """Explicit timeouts; ``read`` (also the write timeout) defaults to the
    domain API timeout."""
    seconds = read if read is not None else settings.domain_api_timeout()
    return httpx.Timeout(
        connect=settings.connect_timeout(),
        read=seconds,
        write=seconds,
        pool=settings.pool_timeout(),
    )


def shared_client() -> httpx.AsyncClient:
    """The running loop's client. Call it from inside a coroutine."""
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=timeout())
        _clients[loop] = client
    return client


async def aclose_shared_client() -> None:
    """Close the running loop's client (application shutdown)."""
    client = _clients.pop(asyncio.get_running_loop(), None)
    if client is not None:
        await client.aclose()


def write_path_headers() -> dict[str, str]:
    """The D24 path of a write made under the current context, as headers.

    Empty outside a dispatched call, or when the context has neither an
    attempt nor a job's run: the Application API then decides (Plan 6 rejects
    an execution write that declares no path).
    """
    try:
        ctx = _toolkit.current_context()
    except _toolkit.NoToolContextError:
        return {}
    if ctx.attempt_id:
        return {ATTEMPT_HEADER: str(ctx.attempt_id)}
    if ctx.job_id and ctx.run_id:
        return {RUN_HEADER: str(ctx.run_id)}
    return {}
