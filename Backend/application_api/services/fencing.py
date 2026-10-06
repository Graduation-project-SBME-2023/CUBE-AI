"""Write paths: every transaction that writes a fenced table declares who
writes, and the shared fencing trigger checks that declaration.

A path is three transaction-local settings, exactly one of them set:

- ``cubeai.attempt_id`` -- the node's own attempt (agent tools, job workers);
- ``cubeai.run_id``     -- an internal job that outlives one attempt;
- ``cubeai.actor``      -- ``user`` (an in-node gate decision) or ``harness``
  (applying a change, starting a node, dispatch, reconcile, the run
  projection, cancellation, suspension, the checkpoint purge).

``fenced_transaction()`` is the one helper that declares a path. It applies to
every transaction the session opens inside the block (``SET LOCAL`` ends with
each commit, so it is re-declared on each begin), and a nested block replaces
the outer path until it exits. Routes on the execution path get their session
from ``fenced_db``, which declares the path the caller sent in ``X-Attempt-Id``
or ``X-Run-Id``; a write that declares none is refused by the database.
"""

from __future__ import annotations

import functools
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, TypeVar
from uuid import UUID

from fastapi import Depends, Header, HTTPException
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.session import get_db

ATTEMPT_HEADER = "X-Attempt-Id"
RUN_HEADER = "X-Run-Id"

_DECLARE = text(
    "SELECT set_config('cubeai.attempt_id', :attempt_id, true), "
    "set_config('cubeai.run_id', :run_id, true), "
    "set_config('cubeai.actor', :actor, true)"
)
_STACK = "cubeai.write_paths"


@dataclass(frozen=True, slots=True)
class WritePath:
    """One declared write path. Build it with the constructors below."""

    attempt_id: UUID | None = None
    run_id: UUID | None = None
    actor: Literal["user", "harness"] | None = None

    def __post_init__(self) -> None:
        if sum(value is not None for value in (self.attempt_id, self.run_id, self.actor)) != 1:
            raise ValueError("a write path declares exactly one of attempt_id, run_id, actor")

    @classmethod
    def attempt(cls, attempt_id: UUID) -> WritePath:
        return cls(attempt_id=attempt_id)

    @classmethod
    def job(cls, run_id: UUID) -> WritePath:
        return cls(run_id=run_id)

    def parameters(self) -> dict[str, str]:
        # An empty setting reads as "not declared" (nullif in the trigger).
        return {
            "attempt_id": str(self.attempt_id or ""),
            "run_id": str(self.run_id or ""),
            "actor": self.actor or "",
        }


USER = WritePath(actor="user")
HARNESS = WritePath(actor="harness")
_UNDECLARED = {"attempt_id": "", "run_id": "", "actor": ""}


def _declare_on_begin(session, _transaction, connection) -> None:
    stack = session.info.get(_STACK)
    if stack:
        connection.execute(_DECLARE, stack[-1].parameters())


@asynccontextmanager
async def fenced_transaction(db: AsyncSession, path: WritePath) -> AsyncIterator[AsyncSession]:
    """Declare ``path`` for every transaction ``db`` runs inside this block."""
    sync = db.sync_session
    if not event.contains(sync, "after_begin", _declare_on_begin):
        event.listen(sync, "after_begin", _declare_on_begin)
    stack: list[WritePath] = sync.info.setdefault(_STACK, [])
    stack.append(path)
    try:
        if db.in_transaction():
            await db.execute(_DECLARE, path.parameters())
        yield db
    except BaseException:
        stack.pop()
        raise
    if db.in_transaction():
        # Writes made under this path reach the database under it; the rest of
        # the open transaction goes back to the outer declaration.
        try:
            await db.flush()
        finally:
            stack.pop()
        await db.execute(_DECLARE, stack[-1].parameters() if stack else _UNDECLARED)
    else:
        stack.pop()


_T = TypeVar("_T")


def on_path(path: WritePath) -> Callable[[Callable[..., Awaitable[_T]]], Callable[..., Awaitable[_T]]]:
    """Run a repository function (first argument: the session) on ``path``."""

    def decorate(function: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
        @functools.wraps(function)
        async def wrapper(db: AsyncSession, *args: Any, **kwargs: Any) -> _T:
            async with fenced_transaction(db, path):
                return await function(db, *args, **kwargs)

        return wrapper

    return decorate


def declared_write_path(
    declared_attempt: UUID | None = Header(None, alias=ATTEMPT_HEADER),
    declared_run: UUID | None = Header(None, alias=RUN_HEADER),
) -> WritePath | None:
    """The path a caller on the execution path declares; None when it
    declares none, in which case the database refuses any fenced write.
    (The parameter names differ from the routes' `run_id` path parameters.)"""
    attempt_id, run_id = declared_attempt, declared_run
    if attempt_id is not None and run_id is not None:
        raise HTTPException(
            status_code=422,
            detail=f"Send {ATTEMPT_HEADER} or {RUN_HEADER}, not both: a write has one path.",
        )
    if attempt_id is not None:
        return WritePath.attempt(attempt_id)
    if run_id is not None:
        return WritePath.job(run_id)
    return None


async def fenced_db(
    path: WritePath | None = Depends(declared_write_path),
    db: AsyncSession = Depends(get_db),
) -> AsyncIterator[AsyncSession]:
    """The request session of a write route on the execution path."""
    if path is None:
        yield db
        return
    async with fenced_transaction(db, path):
        yield db


def require_attempt_header(path: WritePath | None, attempt_id: UUID) -> None:
    """An attempt acts on itself only: the header must name the attempt in the URL."""
    if path is None or path.attempt_id != attempt_id:
        raise HTTPException(
            status_code=403,
            detail=f"Only attempt {attempt_id} may do this; send its id in {ATTEMPT_HEADER}.",
        )
