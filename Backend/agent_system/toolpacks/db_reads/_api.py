"""How the DB read tools call the Application API (merged plan, Phase 2.4, L1).

Every read goes through the shared client with its explicit timeouts. A failure
is an ``error_result`` the model can act on, never an exception: an unreachable
API, a 4xx, a 5xx (whose body is never shown: it may hold stack frames or SQL)
and a body that is not JSON each have their own code.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import httpx

from .. import _toolkit
from ..common.http_client import shared_client, timeout
from ..common.service_urls import app_api_url

_NOT_FOUND = object()


async def get_json(
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    on_not_found: Any = _NOT_FOUND,
) -> Any:
    """The decoded body of ``GET path``, or an ``error_result``.

    A 404 returns ``on_not_found`` when one is given (e.g. ``[]`` for "this
    volume has no mask"), else an error with code ``not_found``.
    """
    try:
        response = await shared_client().get(
            f"{app_api_url()}{path}", params=params, timeout=timeout()
        )
    except httpx.HTTPError as exc:
        return _toolkit.error_result(
            "unavailable", f"GET {path}: the Application API could not be reached ({type(exc).__name__})"
        )
    if response.status_code == 404:
        if on_not_found is not _NOT_FOUND:
            return on_not_found
        return _toolkit.error_result("not_found", f"GET {path}: no such record")
    if response.status_code >= 500:
        return _toolkit.error_result(
            "upstream_error",
            f"GET {path} -> {response.status_code}: the Application API encountered an "
            "internal error and could not complete this request.",
        )
    if response.status_code >= 400:
        return _toolkit.error_result(
            "bad_request", f"GET {path} -> {response.status_code}: {response.text[:400]}"
        )
    try:
        return response.json()
    except ValueError:
        return _toolkit.error_result("invalid_response", f"GET {path}: the response is not JSON")


async def read(
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    shape: Callable[[Any], Any] = lambda body: body,
) -> dict[str, Any]:
    """``{"data": shape(body)}`` (the DB tools' success shape), or an ``error_result``."""
    body = await get_json(path, params=params)
    if _toolkit.is_error_result(body):
        return body
    return {"data": shape(body)}
