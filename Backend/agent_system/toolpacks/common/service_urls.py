"""Where the tool packs reach the other services (merged plan, Phase 2.2).

``app_api_url()`` and ``inference_api_url()`` are the one resolution of each
base URL, read when called. Application traffic never falls back to the
inference service: ``MONAI_URL`` is only an older name of ``INFERENCE_API_URL``.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

DEFAULT_APP_API_URL = "http://localhost:8001"
DEFAULT_INFERENCE_API_URL = "http://localhost:8000"


def app_api_url() -> str:
    """The Application API base URL: ``APP_API_URL``, else ``CONTROL_API_URL``."""
    return _base_url(("APP_API_URL", "CONTROL_API_URL"), DEFAULT_APP_API_URL)


def inference_api_url() -> str:
    """The inference (MONAI Label) base URL: ``INFERENCE_API_URL``, else ``MONAI_URL``."""
    return _base_url(("INFERENCE_API_URL", "MONAI_URL"), DEFAULT_INFERENCE_API_URL)


def agents_base_url() -> str | None:
    """The Agent System's public base URL (``AGENTS_BASE_URL``), or ``None``
    when it is not published."""
    return _base_url(("AGENTS_BASE_URL",), None)


def _base_url(names: tuple[str, ...], default: str | None) -> str | None:
    for name in names:
        raw = os.environ.get(name, "").strip()
        if raw:
            url = raw.rstrip("/")
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError(f"{name}={raw!r} is not an http(s) base URL")
            return url
    return default

