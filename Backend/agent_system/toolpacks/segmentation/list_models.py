"""``list_models``: the segmentation models this deployment enables, as the
inference service describes them."""

from typing import Any

import httpx

from agent_system.agents.execution_agents.segmentation.model_catalog import (
    get_enabled_segmentation_model_paths,
)

from .._toolkit import error_result, tool
from ..common.emit import emit
from ..common.http_client import shared_client
from ..common.manifests import READ_ONLY
from ..common.service_urls import inference_api_url

LIST_TIMEOUT = httpx.Timeout(20.0, connect=5.0)


def _describe(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(model.get("name") or "").strip(),
        "type": model.get("type"),  # automatic / interactive
        "modality": model.get("modality", "unknown"),
        "anatomy": model.get("anatomy", []),
        "labels": model.get("labels", {}),
        "params_schema": model.get("params_schema"),
        "description": model.get("description", ""),
    }


@tool(
    visibility={"agent", "main"},
    timeout_s=30,
    **READ_ONLY,
)
async def list_models():
    """List the enabled segmentation models on the inference service: name,
    type (automatic or interactive), modality, anatomy, labels, params schema
    and description."""
    emit("tool_log", "Fetching models info")
    try:
        response = await shared_client().get(f"{inference_api_url()}/models/", timeout=LIST_TIMEOUT)
    except httpx.HTTPError as exc:
        return error_result("unavailable", f"The inference service could not be reached ({type(exc).__name__})")
    if response.status_code >= 400:
        return error_result("unavailable", f"The inference service did not list its models ({response.status_code})")
    try:
        models = response.json()
    except ValueError:
        return error_result("unavailable", "The inference service's model list is not JSON")

    enabled = {name.casefold() for name in get_enabled_segmentation_model_paths()}
    described = [
        _describe(model)
        for model in models
        if str(model.get("name") or "").strip().casefold() in enabled
    ]
    emit("tool_log", f"Fetched {len(described)} {'model' if len(described) == 1 else 'models'}")
    # One type (L11): the list itself; an error is an error_result, never a bare dict.
    return described
