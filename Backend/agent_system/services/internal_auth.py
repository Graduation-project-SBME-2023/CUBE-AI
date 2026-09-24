"""Shared-secret auth for internal-only routes (`/events`, `/ws-broadcast`).

Both routes exist to receive events from processes the operator controls --
MCP tool subprocesses, each authenticating through
`Backend/mcp/internal_emitter.py` -- but nothing verified that before this
module. In-process agent-system code no longer goes through either route at
all (WS-06 replaced its HTTP self-calls with direct
`socket_store.manager.send_ws_message` calls); these two remain the only
entry points because nothing outside this process can reach the connection
manager any other way. See WEBSOCKET_FIX_PLAN.md W12/WS-01: an
unauthenticated caller could inject events into any session and, via
`/ws-broadcast`'s `agent_waiting_for_user_input` handling, overwrite durable
agent status.

Deliberately fail-closed: `ensure_configured()` refuses to let the process
start unless a token is set, unless the operator explicitly opts out with
`WS_INTERNAL_TOKEN_DEV_BYPASS`. This is the opposite default from
`ORTHANC_WEBHOOK_TOKEN` (application_api/core/config.py), which treats an
unset token as "auth is off" -- that default is what W12 found and is not
repeated here.
"""

import hmac
import logging
import os

from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


def _dev_bypass_enabled() -> bool:
    return os.environ.get("WS_INTERNAL_TOKEN_DEV_BYPASS", "").strip().lower() in _TRUTHY


def _configured_token() -> str:
    return (os.environ.get("WS_INTERNAL_TOKEN") or "").strip()


def ensure_configured() -> None:
    """Refuse to start unless `WS_INTERNAL_TOKEN` is set or the bypass is explicit.

    Call once from `app.py`, after every import -- the same place
    `install_write_guard()` runs -- so a misconfigured deployment fails at
    startup instead of silently accepting unauthenticated internal events.
    """
    if _configured_token():
        return
    if _dev_bypass_enabled():
        logger.warning(
            "WS_INTERNAL_TOKEN is unset and WS_INTERNAL_TOKEN_DEV_BYPASS is set: "
            "/events and /ws-broadcast are accepting UNAUTHENTICATED requests. "
            "Never set this bypass outside local development."
        )
        return
    raise RuntimeError(
        "WS_INTERNAL_TOKEN is unset. /events and /ws-broadcast accept internal "
        "events -- including durable agent-status writes via "
        "agent_waiting_for_user_input -- from any caller that can reach this "
        "port without it. Set WS_INTERNAL_TOKEN in deploy/.env, or set "
        "WS_INTERNAL_TOKEN_DEV_BYPASS=1 to explicitly run without one for "
        "local development only."
    )


async def require_internal_token(x_internal_token: str | None = Header(default=None)) -> None:
    """FastAPI dependency guarding `/events` and `/ws-broadcast`."""
    expected = _configured_token()
    if not expected:
        # Only reachable once ensure_configured() has accepted an explicit
        # dev bypass; a deployment with neither already refused to start.
        return
    if not x_internal_token or not hmac.compare_digest(x_internal_token, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing internal token")
