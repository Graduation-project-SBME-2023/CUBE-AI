"""Lazy, validated settings for the tool packs (merged plan, Phase 2.2).

Every value is read from the environment when it is used, never copied at
import, so a test or a deployment that sets a variable later still sees it. A
missing value takes its default; a value that is not a positive number takes
the default too and logs why, in the style of ``services/timeout_policy.py``.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# The Application API is the control plane: reads and writes must fail fast.
DEFAULT_DOMAIN_API_TIMEOUT_S = 30.0
# Large exports and batch writes (feature sets, model comparisons, artifacts).
MIN_LONG_TIMEOUT_S = 120.0
DEFAULT_CONNECT_TIMEOUT_S = 5.0
DEFAULT_POOL_TIMEOUT_S = 5.0
DEFAULT_LLM_CONCURRENCY = 5


def positive_float(name: str, default: float) -> float:
    """``float(os.environ[name])`` when it is a positive number, else ``default``."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if value > 0:
        return value
    logger.warning("%s=%r is not a positive number; using %s", name, raw, default)
    return default


def domain_api_timeout() -> float:
    """Read and write timeout of one Application API request, in seconds."""
    return positive_float("DOMAIN_API_TIMEOUT_SECONDS", DEFAULT_DOMAIN_API_TIMEOUT_S)


def domain_api_long_timeout() -> float:
    """Timeout of a large export or batch write, never below two minutes."""
    return max(domain_api_timeout(), MIN_LONG_TIMEOUT_S)


def connect_timeout() -> float:
    return positive_float("TOOL_HTTP_CONNECT_SECONDS", DEFAULT_CONNECT_TIMEOUT_S)


def pool_timeout() -> float:
    """How long a request may wait for a free connection in the shared pool."""
    return positive_float("TOOL_HTTP_POOL_SECONDS", DEFAULT_POOL_TIMEOUT_S)


def llm_concurrency_limit() -> int:
    """How many LLM calls one tool call may have in flight at once."""
    # A fraction below one would make a semaphore of zero, which never opens.
    return max(1, int(positive_float("LLM_CONCURRENCY_LIMIT", DEFAULT_LLM_CONCURRENCY)))
