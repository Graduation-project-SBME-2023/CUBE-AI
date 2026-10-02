"""The Application API, as the tools see it.

Identity (session, run, attempt) comes from the current ``ToolContext``, never
from the environment. Requests go through the shared client, and every write
declares its D24 path (``http_client.write_path_headers()``).
"""

from __future__ import annotations

import json as jsonlib
from typing import Any
from urllib.parse import quote

from .. import _toolkit
from . import settings
from .http_client import shared_client, timeout as http_timeout, write_path_headers
from .service_urls import app_api_url

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
POPULATION_COHORT_KEY = "population"
POPULATION_AGENT_TYPE = "study_population_planner_agent"


class DomainApiError(RuntimeError):
    """A domain read or write the Application API refused or could not serve."""

    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _context() -> _toolkit.ToolContext | None:
    try:
        return _toolkit.current_context()
    except _toolkit.NoToolContextError:
        return None


def session_id() -> str:
    """The current call's session, or "" outside a dispatched call."""
    ctx = _context()
    return str(ctx.session_id) if ctx is not None and ctx.session_id else ""


def run_id() -> str:
    """The run of the node this call belongs to, or ""."""
    ctx = _context()
    return str(ctx.run_id) if ctx is not None and ctx.run_id else ""


def attempt_id() -> str:
    """The running attempt of that run (the D24 fence), or ""."""
    ctx = _context()
    return str(ctx.attempt_id) if ctx is not None and ctx.attempt_id else ""


def node_id() -> str:
    """The graph node this call runs in, or ""."""
    ctx = _context()
    return str(ctx.node_id) if ctx is not None and ctx.node_id else ""


def agent_type() -> str:
    """The agent of that node (e.g. ``segmentation_agent``), or ""."""
    ctx = _context()
    return str(ctx.agent_type) if ctx is not None and ctx.agent_type else ""


def require_run() -> str:
    value = run_id()
    if not value:
        raise DomainApiError(
            "This call belongs to no run; only a tool running inside a node can "
            "record domain results."
        )
    return value


def require_session() -> str:
    value = session_id()
    if not value:
        raise DomainApiError("This call belongs to no session (it was not dispatched).")
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
    method = method.upper()
    headers = dict(kwargs.pop("headers", None) or {})
    if method not in READ_METHODS:
        headers.update(write_path_headers())
    response = await shared_client().request(
        method,
        f"{app_api_url()}{path}",
        json=json,
        params=params,
        headers=headers,
        timeout=http_timeout(timeout),
        **kwargs,
    )
    if allow_404 and response.status_code == 404:
        return None
    if response.status_code >= 400:
        try:
            body = response.json()
        except ValueError:
            body = response.text
        detail = body.get("detail") if isinstance(body, dict) else body
        if response.status_code >= 500:
            # A 5xx detail is an unhandled server exception (stack frames, raw
            # SQL, column names) -- useful in the Application API's own logs,
            # never something an agent should see: it ends up quoted verbatim
            # in scratchpad flags and, from there, in generated reports.
            message = (
                f"{method} {path} -> {response.status_code}: the Application API "
                "encountered an internal error and could not complete this request."
            )
        else:
            message = f"{method} {path} -> {response.status_code}: {str(detail)[:600]}"
        raise DomainApiError(message, status_code=response.status_code, body=body)
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
    if not for_session and run_id():
        run = await request("GET", f"/runs/{run_id()}")
        if (run.get("node") or {}).get("target_name") == POPULATION_AGENT_TYPE:
            return str(run["run_id"])
    resolved = await request(
        "GET", f"/runs/session/{target_session}/by-target/{POPULATION_AGENT_TYPE}"
    )
    return str(resolved["run"]["run_id"])


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
        "GET", f"/segmentation-batches/session/{for_session or require_session()}/reviewed-masks"
    )


# ----------------------------------------------------------------------
# Feature extraction (step I)
# ----------------------------------------------------------------------
# Large enough that writing a cohort takes a handful of requests, small enough
# that a batch stays well under the API's per-request row bound.
RADIOMICS_BATCH_SIZE = 50


async def record_radiomics_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """One all-or-nothing batch; identical replays are reused, not duplicated."""
    return await request(
        "POST",
        f"/radiomics/run/{require_run()}/batch",
        json={"rows": rows},
        timeout=settings.domain_api_long_timeout(),
    )


async def run_radiomics_rows(
    run_id: str | None = None, *, include_features: bool = True
) -> list[dict[str, Any]]:
    """Every row one run recorded, paged."""
    run_id = run_id or require_run()
    rows: list[dict[str, Any]] = []
    skip, page = 0, 500
    while True:
        batch = await request(
            "GET",
            f"/radiomics/session/{require_session()}",
            params={
                "run_id": run_id,
                "include_features": "true" if include_features else "false",
                "skip": skip,
                "limit": page,
            },
            timeout=settings.domain_api_long_timeout(),
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
        timeout=settings.domain_api_long_timeout(),
    )


async def freeze_feature_set(feature_set_id: str) -> dict[str, Any]:
    return await request("POST", f"/feature-sets/{feature_set_id}/freeze", timeout=settings.domain_api_long_timeout())


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
        timeout=settings.domain_api_long_timeout(),
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
        timeout=settings.domain_api_long_timeout(),
    )


async def model_results_summary(for_session: str | None = None) -> list[dict[str, Any]]:
    return await request(
        "GET", f"/model-results/session/{for_session or require_session()}/summary"
    )


async def latest_literature_review(for_session: str | None = None) -> dict[str, Any] | None:
    """This session's most recent literature review, with its papers — the
    durable replacement for the retired workspace `literature_context.json`.
    `None` if the planner never ran the literature review phase."""
    summaries = await request(
        "GET", f"/literature-reviews/session/{for_session or require_session()}"
    )
    if not summaries:
        return None
    latest = max(summaries, key=lambda item: item["created_at"])
    return await request("GET", f"/literature-reviews/{latest['literature_review_id']}")


async def list_session_artifacts(kind: str | None = None) -> list[dict[str, Any]]:
    """This session's artifact catalogue (no bytes — `artifact_content` fetches
    those one at a time). `kind` restricts to one artifact kind, e.g.
    `figure_png`; omit it to list every kind."""
    params = {"kind": kind} if kind else None
    return await request(
        "GET", f"/files/session/{require_session()}", params=params, timeout=settings.domain_api_long_timeout()
    )


async def artifact_content(file_id: str) -> bytes:
    """Retained bytes, e.g. a model's per-case predictions."""
    response = await shared_client().get(
        f"{app_api_url()}/files/{file_id}/content",
        timeout=http_timeout(settings.domain_api_long_timeout()),
    )
    if response.status_code >= 400:
        raise DomainApiError(
            f"GET /files/{file_id}/content -> {response.status_code}",
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
    """Store retained bytes in `stored_files.body`."""
    form: dict[str, str] = {
        "session_id": require_session(),
        "logical_key": logical_key,
        "kind": kind,
        "display_filename": filename,
    }
    if run_id():
        form["created_by_run_id"] = run_id()
    if metadata:
        form["metadata"] = jsonlib.dumps(metadata, allow_nan=False)
    return await request(
        "POST",
        "/files/",
        data=form,
        files={"file": (filename, payload, mime_type)},
        timeout=settings.domain_api_long_timeout(),
    )
