"""The full-sync advisory lock must stay on one connection for the whole sync.

``sync_all_from_orthanc`` commits after every study, which returns the sync
session's connection to the pool. A session-level advisory lock taken through
that session left with the pooled connection, and the unlock ran on another one.
"""

from __future__ import annotations

import pytest

sync = pytest.importorskip("application_api.api.routes.sync")

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _LockConnection:
    def __init__(self, lock_free=True, unlock_error=None):
        self.statements: list[str] = []
        self.options: dict = {}
        self.invalidated = False
        self.closed = False
        self._lock_free = lock_free
        self._unlock_error = unlock_error

    async def execution_options(self, **options):
        self.options.update(options)
        return self

    async def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if "pg_advisory_unlock" in sql:
            if self._unlock_error is not None:
                raise self._unlock_error
            return _Result(True)
        return _Result(self._lock_free)

    async def invalidate(self):
        self.invalidated = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False


class _Engine:
    def __init__(self, conn):
        self.conn = conn

    def connect(self):
        return self.conn


class _SyncSession:
    """Any statement through the sync session is a failure for these tests."""

    async def execute(self, statement):  # pragma: no cover - asserted against
        raise AssertionError(f"the lock must not go through the sync session: {statement}")


def _install(monkeypatch, conn, on_sync=None):
    calls = []

    async def _fake_sync_all(db):
        calls.append(db)
        if on_sync is not None:
            await on_sync()

    monkeypatch.setattr(sync, "engine", _Engine(conn))
    monkeypatch.setattr(sync, "sync_all_from_orthanc", _fake_sync_all)
    return calls


async def test_lock_and_unlock_share_a_dedicated_autocommit_connection(monkeypatch):
    conn = _LockConnection()
    held_during_sync = []

    async def _check_held():
        held_during_sync.append(not conn.closed and len(conn.statements) == 1)

    calls = _install(monkeypatch, conn, on_sync=_check_held)
    session = _SyncSession()

    assert await sync._run_full_sync_once(session) is True

    assert calls == [session], "the sync itself still runs on the caller's session"
    assert held_during_sync == [True], "the lock connection stays open for the whole sync"
    assert conn.options.get("isolation_level") == "AUTOCOMMIT"
    assert len(conn.statements) == 2
    assert "pg_try_advisory_lock" in conn.statements[0]
    assert "pg_advisory_unlock" in conn.statements[1]
    assert conn.closed and not conn.invalidated


async def test_skips_when_another_sync_holds_the_lock(monkeypatch):
    conn = _LockConnection(lock_free=False)
    calls = _install(monkeypatch, conn)

    assert await sync._run_full_sync_once(_SyncSession()) is False

    assert calls == []
    assert len(conn.statements) == 1, "nothing to unlock when the lock was not taken"


async def test_unlocks_even_when_the_sync_fails(monkeypatch):
    conn = _LockConnection()

    async def _boom():
        raise RuntimeError("orthanc went away")

    _install(monkeypatch, conn, on_sync=_boom)

    with pytest.raises(RuntimeError, match="orthanc went away"):
        await sync._run_full_sync_once(_SyncSession())

    assert "pg_advisory_unlock" in conn.statements[-1]


async def test_a_failed_unlock_drops_the_connection_instead_of_pooling_the_lock(monkeypatch):
    conn = _LockConnection(unlock_error=ConnectionError("connection reset"))
    _install(monkeypatch, conn)

    assert await sync._run_full_sync_once(_SyncSession()) is True

    assert conn.invalidated, "a connection that may still hold the lock must not be pooled"
