"""How the DB read tools call the Application API (merged plan, Phase 2.4, L1).

Every read goes through ``domain_api.send()`` (the shared client, explicit
timeouts). A failure is an ``error_result`` the model can act on, never an
exception: an unreachable API, a 4xx, a 5xx (whose body is never shown: it may
hold stack frames or SQL) and a body that is not JSON each have their own code.

``Skip`` and ``Limit`` are the paging arguments every listing tool takes; a
page is at most ``MAX_PAGE`` rows.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Annotated, Any

from pydantic import Field

from .. import _toolkit
from ..common import domain_api

MAX_PAGE = 100
_NOT_FOUND = object()

Skip = Annotated[int, Field(ge=0, description="Pagination offset: the number of records to skip.")]
Limit = Annotated[
    int,
    Field(ge=1, le=MAX_PAGE, description=f"Pagination limit: the maximum number of records to return (max {MAX_PAGE})."),
]

segment = domain_api.segment


def page(skip: int, limit: int) -> dict[str, int]:
    return {"skip": skip, "limit": limit}


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
        response = await domain_api.send("GET", path, params=dict(params or {}))
        if response.status_code == 404:
            if on_not_found is not _NOT_FOUND:
                return on_not_found
            return _toolkit.error_result("not_found", f"GET {path}: no such record")
        domain_api.raise_for_status(response, "GET", path)
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc)
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


def query(**params: Any) -> dict[str, Any]:
    """The query parameters that were given (``None`` means "not given")."""
    return {name: value for name, value in params.items() if value is not None}
