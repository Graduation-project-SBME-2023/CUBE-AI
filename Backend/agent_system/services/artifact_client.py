"""Publishing artifacts from the agent system"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from services.timeout_policy import CONTROL_HTTP_TIMEOUT

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get(
    "APP_API_URL",
    os.environ.get("CONTROL_API_URL", "http://localhost:8001"),
).rstrip("/")

# A retained upload can be up to the configured cap (64 MiB for CSV), so it
# gets more room than a control call.
UPLOAD_TIMEOUT = float(os.environ.get("ARTIFACT_UPLOAD_TIMEOUT_SECONDS", "120"))


async def upload_bytes(
    session_id: str,
    logical_key: str,
    kind: str,
    payload: bytes,
    *,
    display_filename: str | None = None,
    mime_type: str = "application/octet-stream",
    agent_run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish retained bytes and return the catalogue row."""
    import json

    form: dict[str, Any] = {
        "session_id": str(session_id),
        "logical_key": logical_key,
        "kind": kind,
        "display_filename": display_filename or logical_key,
    }
    if agent_run_id:
        form["created_by_agent_run_id"] = str(agent_run_id)
    if metadata is not None:
        form["metadata"] = json.dumps(metadata)

    async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT) as client:
        response = await client.post(
            f"{BASE_URL}/artifacts/",
            data=form,
            files={"file": (display_filename or logical_key, payload, mime_type)},
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Artifact {logical_key!r} was not stored "
                f"({response.status_code}): {response.text[:400]}"
            )
        return response.json()


async def upload_file(
    session_id: str,
    logical_key: str,
    kind: str,
    path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    with open(path, "rb") as handle:
        payload = handle.read()
    kwargs.setdefault("display_filename", os.path.basename(path))
    return await upload_bytes(session_id, logical_key, kind, payload, **kwargs)


async def register_render(
    session_id: str,
    logical_key: str,
    render_path: str,
    *,
    kind: str = "figure_gif",
    mime_type: str = "image/gif",
    display_filename: str | None = None,
    agent_run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Catalogue a regenerable render. Returns None if it could not be catalogued — the render itself is unaffected, because it is a cache entry."""
    payload: dict[str, Any] = {
        "session_id": str(session_id),
        "logical_key": logical_key,
        "render_path": render_path.replace("\\", "/"),
        "kind": kind,
        "mime_type": mime_type,
    }
    if display_filename:
        payload["display_filename"] = display_filename
    if agent_run_id:
        payload["created_by_agent_run_id"] = str(agent_run_id)
    if metadata is not None:
        payload["metadata"] = metadata

    try:
        async with httpx.AsyncClient(timeout=CONTROL_HTTP_TIMEOUT) as client:
            response = await client.post(f"{BASE_URL}/artifacts/renders", json=payload)
            if response.status_code == 409:
                logger.info(
                    "Render %s is already catalogued with different content; the "
                    "preview still works, the catalogue row was not changed.",
                    logical_key,
                )
                return None
            response.raise_for_status()
            return response.json()
    except Exception as exc:  # noqa: BLE001 - a catalogue miss is not data loss
        logger.warning("Could not catalogue render %s: %s", logical_key, exc)
        return None


def render_logical_key(pair_id: str, artifact_key: str) -> str:
    """One stable key per pair's overlay."""
    return f"overlay/{artifact_key or pair_id}"
