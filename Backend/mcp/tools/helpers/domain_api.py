"""The Application API, as the MCP tools see it."""

from __future__ import annotations

import json as jsonlib
import os
from typing import Any
from urllib.parse import quote

import httpx

REQUEST_TIMEOUT = float(os.environ.get("DOMAIN_API_TIMEOUT_SECONDS", "30"))
POPULATION_COHORT_KEY = "population"
POPULATION_AGENT_TYPE = "study_population_planner_agent"


class DomainApiError(RuntimeError):
    """A domain read or write the Application API refused or could not serve."""

    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def app_api_url() -> str:
    """Same resolution as `service_urls` (importable only inside the MCP
    subprocess, where Backend/mcp is on sys.path)."""
    try:
        from service_urls import APP_API_URL

        return APP_API_URL
    except ImportError:
        return os.environ.get(
            "APP_API_URL", os.environ.get("CONTROL_API_URL", "http://localhost:8001")
        ).rstrip("/")


def session_id() -> str:
    return os.environ.get("SESSION_ID", "")


def agent_run_id() -> str:
    return os.environ.get("AGENT_RUN_ID", "")


def require_run() -> str:
    run_id = agent_run_id()
    if not run_id:
        raise DomainApiError(
            "AGENT_RUN_ID is not set in the MCP environment; this tool is not bound to "
            "an approved plan step and cannot record domain results."
        )
    return run_id


def require_session() -> str:
    value = session_id()
    if not value:
        raise DomainApiError("SESSION_ID is not set in the MCP environment.")
    return value


async def request(
    method: str,
    path: str,
    *,
    json: Any = None,
    params: dict[str, Any] | None = None,
    allow_404: bool = False,
    timeout: float | None = None,
    **kwargs: Any,
) -> Any:
    async with httpx.AsyncClient(timeout=timeout or REQUEST_TIMEOUT) as client:
        response = await client.request(
            method, f"{app_api_url()}{path}", json=json, params=params, **kwargs
        )
    if allow_404 and response.status_code == 404:
        return None
    if response.status_code >= 400:
        try:
            body = response.json()
        except ValueError:
            body = response.text
        detail = body.get("detail") if isinstance(body, dict) else body
        raise DomainApiError(
            f"{method} {path} -> {response.status_code}: {str(detail)[:600]}",
            status_code=response.status_code,
            body=body,
        )
    if not response.content:
        return None
    content_type = response.headers.get("content-type", "")
    return response.json() if "json" in content_type else response.text


# ----------------------------------------------------------------------
# Cohorts (step H)
# ----------------------------------------------------------------------
async def population_run_id(for_session: str | None = None) -> str:
    """The run whose cohort this session's population is. Inside the population agent it is simply this run."""
    target_session = for_session or require_session()
    if not for_session and agent_run_id():
        run = await request("GET", f"/agent-runs/{agent_run_id()}")
        if (run.get("step") or {}).get("agent_type") == POPULATION_AGENT_TYPE:
            return str(run["agent_run_id"])
    resolved = await request(
        "GET", f"/agent-runs/session/{target_session}/by-agent-type/{POPULATION_AGENT_TYPE}"
    )
    return str(resolved["run"]["agent_run_id"])


async def upsert_population_cohort(
    *,
    criteria: dict[str, Any] | None = None,
    selection_summary: dict[str, Any] | None = None,
    for_session: str | None = None,
) -> dict[str, Any]:
    run_id = await population_run_id(for_session)
    return await request(
        "PUT",
        f"/cohorts/run/{run_id}/{POPULATION_COHORT_KEY}",
        json={"criteria": criteria, "selection_summary": selection_summary},
    )


async def get_population_cohort(for_session: str | None = None) -> dict[str, Any] | None:
    return await request(
        "GET",
        f"/cohorts/session/{for_session or require_session()}/population",
        allow_404=True,
    )


async def require_population_cohort(for_session: str | None = None) -> dict[str, Any]:
    cohort = await get_population_cohort(for_session)
    if cohort is None:
        raise DomainApiError(
            "The population step has not defined a cohort yet. Run the population "
            "planner (write_inclusion_criteria / validate_population_anatomy) first."
        )
    return cohort


async def set_cohort_members(cohort_id: str, volume_uids: list[str]) -> dict[str, Any]:
    return await request(
        "PUT",
        f"/cohorts/{cohort_id}/members",
        json={"volume_series_instance_uids": list(volume_uids)},
    )


async def write_cohort_labels(cohort_id: str, labels: list[dict[str, Any]]) -> dict[str, Any]:
    return await request("PATCH", f"/cohorts/{cohort_id}/labels", json={"labels": labels})


async def cohort_analysis_rows(cohort_id: str) -> list[dict[str, Any]]:
    return await request("GET", f"/cohorts/{cohort_id}/analysis-rows")


# ----------------------------------------------------------------------
# Segmentation review (step H) — read by feature extraction
# ----------------------------------------------------------------------
async def reviewed_masks(for_session: str | None = None) -> list[dict[str, Any]]:
    return await request(
        "GET", f"/segmentation-runs/session/{for_session or require_session()}/reviewed-masks"
    )


# ----------------------------------------------------------------------
# Feature extraction (step I)
# ----------------------------------------------------------------------
# Large enough that writing a cohort takes a handful of requests, small enough
# that a batch stays well under the API's per-request row bound.
RADIOMICS_BATCH_SIZE = 50
_LONG_TIMEOUT = max(REQUEST_TIMEOUT, 120.0)


async def record_radiomics_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """One all-or-nothing batch; identical replays are reused, not duplicated."""
    return await request(
        "POST",
        f"/radiomics/run/{require_run()}/batch",
        json={"rows": rows},
        timeout=_LONG_TIMEOUT,
    )


async def run_radiomics_rows(
    agent_run_id: str | None = None, *, include_features: bool = True
) -> list[dict[str, Any]]:
    """Every row one run recorded, paged."""
    run_id = agent_run_id or require_run()
    rows: list[dict[str, Any]] = []
    skip, page = 0, 500
    while True:
        batch = await request(
            "GET",
            f"/radiomics/session/{require_session()}",
            params={
                "agent_run_id": run_id,
                "include_features": "true" if include_features else "false",
                "skip": skip,
                "limit": page,
            },
            timeout=_LONG_TIMEOUT,
        ) or []
        rows.extend(batch)
        if len(batch) < page:
            return rows
        skip += page


async def create_feature_set(set_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await request(
        "PUT",
        f"/feature-sets/run/{require_run()}/{quote(set_key, safe='')}",
        json=payload,
        timeout=_LONG_TIMEOUT,
    )


async def freeze_feature_set(feature_set_id: str) -> dict[str, Any]:
    return await request("POST", f"/feature-sets/{feature_set_id}/freeze", timeout=_LONG_TIMEOUT)


async def list_feature_sets(for_session: str | None = None) -> list[dict[str, Any]]:
    return await request("GET", f"/feature-sets/session/{for_session or require_session()}")


async def get_feature_set(feature_set_id: str) -> dict[str, Any]:
    return await request("GET", f"/feature-sets/{feature_set_id}")


async def export_feature_set(
    feature_set_id: str, *, part: str = "all", fmt: str = "csv"
) -> Any:
    """The modeling handoff: features plus the cohort label joined at read time."""
    return await request(
        "GET",
        f"/feature-sets/{feature_set_id}/export",
        params={"part": part, "format": fmt},
        timeout=_LONG_TIMEOUT,
    )


# ----------------------------------------------------------------------
# Modeling (step J)
# ----------------------------------------------------------------------
async def get_model_comparison(comparison_key: str) -> dict[str, Any] | None:
    """The recorded comparison of this run, if a replay already produced it."""
    return await request(
        "GET",
        f"/model-results/run/{require_run()}/{quote(comparison_key, safe='')}",
        allow_404=True,
    )


async def record_model_comparison(comparison_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Every variant of one comparison, in one transaction (insert-or-read)."""
    return await request(
        "PUT",
        f"/model-results/run/{require_run()}/{quote(comparison_key, safe='')}",
        json=payload,
        timeout=_LONG_TIMEOUT,
    )


async def model_results_summary(for_session: str | None = None) -> list[dict[str, Any]]:
    return await request(
        "GET", f"/model-results/session/{for_session or require_session()}/summary"
    )


async def artifact_content(artifact_id: str) -> bytes:
    """Retained bytes, e.g. a model's per-case predictions."""
    async with httpx.AsyncClient(timeout=_LONG_TIMEOUT) as client:
        response = await client.get(f"{app_api_url()}/artifacts/{artifact_id}/content")
    if response.status_code >= 400:
        raise DomainApiError(
            f"GET /artifacts/{artifact_id}/content -> {response.status_code}",
            status_code=response.status_code,
            body=response.text[:600],
        )
    return response.content


# ----------------------------------------------------------------------
# Artifacts (step G) — retained figure and CSV bytes
# ----------------------------------------------------------------------
async def publish_artifact(
    *,
    logical_key: str,
    kind: str,
    payload: bytes,
    filename: str,
    mime_type: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store retained bytes in `artifacts.body`."""
    form: dict[str, str] = {
        "session_id": require_session(),
        "logical_key": logical_key,
        "kind": kind,
        "display_filename": filename,
    }
    if agent_run_id():
        form["created_by_agent_run_id"] = agent_run_id()
    if metadata:
        form["metadata"] = jsonlib.dumps(metadata, allow_nan=False)
    return await request(
        "POST",
        "/artifacts/",
        data=form,
        files={"file": (filename, payload, mime_type)},
        timeout=_LONG_TIMEOUT,
    )
