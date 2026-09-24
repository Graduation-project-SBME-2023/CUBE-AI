"""services/internal_auth.py -- the WS_INTERNAL_TOKEN gate on /events and
/ws-broadcast (WEBSOCKET_FIX_PLAN.md W12/WS-01).

conftest.py sets WS_INTERNAL_TOKEN="test-internal-token" for every test so
importing agent_system's app.py doesn't refuse to start; these tests
monkeypatch it away one at a time to exercise the unset/bypass paths.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException

from services import internal_auth

pytestmark = pytest.mark.unit


# ----------------------------------------------------------------------
# ensure_configured() -- the fail-closed startup check
# ----------------------------------------------------------------------


def test_ensure_configured_passes_when_token_is_set(monkeypatch):
    monkeypatch.setenv("WS_INTERNAL_TOKEN", "a-real-secret")
    monkeypatch.delenv("WS_INTERNAL_TOKEN_DEV_BYPASS", raising=False)

    internal_auth.ensure_configured()  # must not raise


def test_ensure_configured_refuses_to_start_when_token_unset(monkeypatch):
    monkeypatch.delenv("WS_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("WS_INTERNAL_TOKEN_DEV_BYPASS", raising=False)

    with pytest.raises(RuntimeError, match="WS_INTERNAL_TOKEN"):
        internal_auth.ensure_configured()


@pytest.mark.parametrize("blank", ["", "   "])
def test_ensure_configured_treats_a_blank_token_as_unset(monkeypatch, blank):
    """compose injects an unset key as "", same hazard config.py documents
    for ORTHANC_WEBHOOK_TOKEN -- must not be read as "a token is configured"."""
    monkeypatch.setenv("WS_INTERNAL_TOKEN", blank)
    monkeypatch.delenv("WS_INTERNAL_TOKEN_DEV_BYPASS", raising=False)

    with pytest.raises(RuntimeError, match="WS_INTERNAL_TOKEN"):
        internal_auth.ensure_configured()


def test_ensure_configured_allows_the_explicit_dev_bypass(monkeypatch, caplog):
    monkeypatch.delenv("WS_INTERNAL_TOKEN", raising=False)
    monkeypatch.setenv("WS_INTERNAL_TOKEN_DEV_BYPASS", "1")

    with caplog.at_level(logging.WARNING, logger=internal_auth.__name__):
        internal_auth.ensure_configured()  # must not raise

    assert "UNAUTHENTICATED" in caplog.text


# ----------------------------------------------------------------------
# require_internal_token() -- the per-request dependency
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_internal_token_rejects_a_missing_header(monkeypatch):
    monkeypatch.setenv("WS_INTERNAL_TOKEN", "a-real-secret")

    with pytest.raises(HTTPException) as excinfo:
        await internal_auth.require_internal_token(x_internal_token=None)
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_require_internal_token_rejects_the_wrong_token(monkeypatch):
    monkeypatch.setenv("WS_INTERNAL_TOKEN", "a-real-secret")

    with pytest.raises(HTTPException) as excinfo:
        await internal_auth.require_internal_token(x_internal_token="not-it")
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_require_internal_token_accepts_the_configured_token(monkeypatch):
    monkeypatch.setenv("WS_INTERNAL_TOKEN", "a-real-secret")

    await internal_auth.require_internal_token(x_internal_token="a-real-secret")  # no raise


@pytest.mark.asyncio
async def test_require_internal_token_passes_through_under_the_dev_bypass(monkeypatch):
    """Only reachable once ensure_configured() has accepted an explicit
    bypass; a deployment with neither already refused to start."""
    monkeypatch.delenv("WS_INTERNAL_TOKEN", raising=False)
    monkeypatch.setenv("WS_INTERNAL_TOKEN_DEV_BYPASS", "1")

    await internal_auth.require_internal_token(x_internal_token=None)  # no raise
