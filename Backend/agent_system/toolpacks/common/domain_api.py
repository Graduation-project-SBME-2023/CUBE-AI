"""The Application API, as the tools see it.

Identity (session, run, attempt) comes from the current ``ToolContext``, never
from the environment. Every request goes through ``send()``: the shared
client, explicit timeouts, and the D24 write path on every write
(``http_client.write_path_headers()``). ``request()`` adds the error mapping
and decodes the body; the domain functions below name the routes the tools use.

Interpolate a caller-supplied value into a path only through ``segment()``, so
it can never add a path segment or a query string.
"""

from __future__ import annotations

import json as jsonlib
from typing import Any
from urllib.parse import quote

import httpx

from .. import _toolkit
from . import settings
from .http_client import shared_client, timeout as http_timeout, write_path_headers
from .service_urls import app_api_url

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
POPULATION_COHORT_KEY = "population"
POPULATION_AGENT_TYPE = "study_population_planner_agent"
DETAIL_CHARS = 600


class DomainApiError(RuntimeError):
    """A domain read or write the Application API refused or could not serve.
    ``status_code`` is ``None`` when the API could not be reached at all."""

    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body

    @property
    def error_code(self) -> str:
        """The ``error_result`` code for this failure."""
        if self.status_code is None:
            return "unavailable"
        if self.status_code == 404:
            return "not_found"
        if self.status_code == 409:
            return "conflict"
        return "upstream_error" if self.status_code >= 500 else "bad_request"


def error_result_from(exc: DomainApiError, context: str | None = None, **extra: Any) -> dict[str, Any]:
    """``exc`` as the tools' error envelope; ``context`` says what failed."""
    message = f"{context}: {exc}" if context else str(exc)
    return _toolkit.error_result(exc.error_code, message, **extra)


# ----------------------------------------------------------------------
# Identity, from the current call's context
# ----------------------------------------------------------------------
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


# ----------------------------------------------------------------------
# Transport
# ----------------------------------------------------------------------
def segment(value: object) -> str:
    """``value`` as one URL path segment: ``/``, ``?``, ``#`` and ``%`` are escaped."""
    return quote(str(value), safe="")


async def send(
    method: str,
    path: str,
    *,
    json: Any = None,
    params: dict[str, Any] | None = None,
    timeout: float | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """One request to the Application API; the response whatever its status.

    Raises ``DomainApiError`` (status ``None``) only when the API cannot be
    reached. Use ``request()`` unless the caller reads non-JSON bodies or
    error details itself.
    """
    method = method.upper()
    headers = dict(kwargs.pop("headers", None) or {})
    if method not in READ_METHODS:
        headers.update(write_path_headers())
    try:
        return await shared_client().request(
            method,
            f"{app_api_url()}{path}",
            json=json,
            params=params,
            headers=headers,
            timeout=http_timeout(timeout),
            **kwargs,
        )
    except httpx.HTTPError as exc:
        raise DomainApiError(
            f"{method} {path}: the Application API could not be reached ({type(exc).__name__})"
        ) from exc


def response_body(response: httpx.Response) -> Any:
    """The decoded body: JSON when it is JSON, else the text."""
    try:
        return response.json()
    except ValueError:
        return response.text


def raise_for_status(response: httpx.Response, method: str, path: str) -> None:
    """``DomainApiError`` for a 4xx or 5xx response. A 4xx carries the API's
    ``detail``; a 5xx never does: its body is an unhandled server exception
    (stack frames, raw SQL), which an agent would quote into a report."""
    if response.status_code < 400:
        return
    body = response_body(response)
    if response.status_code >= 500:
        message = (
            f"{method.upper()} {path} -> {response.status_code}: the Application API "
            "encountered an internal error and could not complete this request."
        )
    else:
        detail = body.get("detail") if isinstance(body, dict) else body
        message = f"{method.upper()} {path} -> {response.status_code}: {str(detail)[:DETAIL_CHARS]}"
    raise DomainApiError(message, status_code=response.status_code, body=body)


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
    """The decoded body of a successful request (JSON, or text for any other
    content type, ``None`` when empty); ``None`` for a 404 when ``allow_404``.
    Every failure raises ``DomainApiError``."""
    response = await send(method, path, json=json, params=params, timeout=timeout, **kwargs)
    if allow_404 and response.status_code == 404:
        return None
    raise_for_status(response, method, path)
    if not response.content:
        return None
    if "json" in response.headers.get("content-type", ""):
        return response.json()
    return response.text


# ----------------------------------------------------------------------
# Runs (the node's own row: output, scratchpad, task)
# ----------------------------------------------------------------------
async def get_session(for_session: str | None = None) -> dict[str, Any]:
    return await request("GET", f"/sessions/{segment(for_session or require_session())}")


async def get_run(run: str | None = None) -> dict[str, Any]:
    return await request("GET", f"/runs/{segment(run or require_run())}")


async def session_runs(for_session: str | None = None) -> list[dict[str, Any]]:
    """Every run of the session, in plan order."""
    return await request("GET", f"/runs/session/{segment(for_session or require_session())}") or []


async def run_by_target(target_name: str, for_session: str | None = None) -> dict[str, Any] | None:
    """``{"run": ..., "nodes_sharing_this_target": n}`` for the first node in
    plan order whose target is ``target_name``, or ``None`` when none is."""
    return await request(
        "GET",
        f"/runs/session/{segment(for_session or require_session())}/by-target/{segment(target_name)}",
        allow_404=True,
    )


async def record_run_output(output: dict[str, Any]) -> dict[str, Any]:
    """This run's structured output (``write_agent_output``'s row)."""
    return await request("PUT", f"/runs/{segment(require_run())}/output", json=output)


# ----------------------------------------------------------------------
# Cohorts (step H)
# ----------------------------------------------------------------------
async def population_run_id(for_session: str | None = None) -> str:
    """The run whose cohort this session's population is. Inside the population agent it is simply this run."""
    target_session = for_session or require_session()
    if not for_session and run_id():
        run = await get_run()
        if (run.get("node") or {}).get("target_name") == POPULATION_AGENT_TYPE:
            return str(run["run_id"])
    resolved = await request(
        "GET",
        f"/runs/session/{segment(target_session)}/by-target/{POPULATION_AGENT_TYPE}",
    )
    return str(resolved["run"]["run_id"])


async def upsert_population_cohort(
    *,
    criteria: dict[str, Any] | None = None,
    selection_summary: dict[str, Any] | None = None,
    for_session: str | None = None,
) -> dict[str, Any]:
    run = await population_run_id(for_session)
    return await request(
        "PUT",
        f"/cohorts/run/{segment(run)}/{POPULATION_COHORT_KEY}",
        json={"criteria": criteria, "selection_summary": selection_summary},
    )


async def get_population_cohort(for_session: str | None = None) -> dict[str, Any] | None:
    return await request(
        "GET",
        f"/cohorts/session/{segment(for_session or require_session())}/population",
        allow_404=True,
    )


async def require_population_cohort(for_session: str | None = None) -> dict[str, Any]:
    cohort = await get_population_cohort(for_session)
    if cohort is None:
        raise DomainApiError(
            "The population step has not defined a cohort yet. Run the population "
            "planner (write_inclusion_criteria / validate_population_anatomy) first.",
            status_code=404,  # no cohort is "not found", not an unreachable API
        )
    return cohort


async def set_cohort_members(cohort_id: str, volume_uids: list[str]) -> dict[str, Any]:
    return await request(
        "PUT",
        f"/cohorts/{segment(cohort_id)}/members",
        json={"volume_series_instance_uids": list(volume_uids)},
    )


async def write_cohort_labels(cohort_id: str, labels: list[dict[str, Any]]) -> dict[str, Any]:
    return await request("PATCH", f"/cohorts/{segment(cohort_id)}/labels", json={"labels": labels})


async def cohort_analysis_rows(cohort_id: str) -> list[dict[str, Any]]:
    return await request("GET", f"/cohorts/{segment(cohort_id)}/analysis-rows")


async def get_patient(subject_key: str) -> dict[str, Any] | None:
    return await request("GET", f"/patients/{segment(subject_key)}", allow_404=True)


# ----------------------------------------------------------------------
# Segmentation review (step H) — read by feature extraction
# ----------------------------------------------------------------------
async def reviewed_masks(for_session: str | None = None) -> list[dict[str, Any]]:
    return await request(
        "GET",
        f"/segmentation-batches/session/{segment(for_session or require_session())}/reviewed-masks",
    )


# ----------------------------------------------------------------------
# Feature extraction (step I)
# ----------------------------------------------------------------------
# Large enough that writing a cohort takes a handful of requests, small enough
# that a batch stays well under the API's per-request row bound.
RADIOMICS_BATCH_SIZE = 50
RADIOMICS_PAGE_SIZE = 500


async def record_radiomics_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """One all-or-nothing batch; identical replays are reused, not duplicated."""
    return await request(
        "POST",
        f"/radiomics/run/{segment(require_run())}/batch",
        json={"rows": rows},
        timeout=settings.domain_api_long_timeout(),
    )


async def run_radiomics_rows(
    run: str | None = None, *, include_features: bool = True
) -> list[dict[str, Any]]:
    """Every row one run recorded, paged."""
    run = run or require_run()
    rows: list[dict[str, Any]] = []
    skip = 0
    while True:
        batch = await request(
            "GET",
            f"/radiomics/session/{segment(require_session())}",
            params={
                "run_id": run,
                "include_features": "true" if include_features else "false",
                "skip": skip,
                "limit": RADIOMICS_PAGE_SIZE,
            },
            timeout=settings.domain_api_long_timeout(),
        ) or []
        rows.extend(batch)
        if len(batch) < RADIOMICS_PAGE_SIZE:
            return rows
        skip += RADIOMICS_PAGE_SIZE


async def create_feature_set(set_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await request(
        "PUT",
        f"/feature-sets/run/{segment(require_run())}/{segment(set_key)}",
        json=payload,
        timeout=settings.domain_api_long_timeout(),
    )


async def freeze_feature_set(feature_set_id: str) -> dict[str, Any]:
    return await request(
        "POST",
        f"/feature-sets/{segment(feature_set_id)}/freeze",
        timeout=settings.domain_api_long_timeout(),
    )


async def list_feature_sets(for_session: str | None = None) -> list[dict[str, Any]]:
    return await request("GET", f"/feature-sets/session/{segment(for_session or require_session())}")


async def get_feature_set(feature_set_id: str) -> dict[str, Any]:
    return await request("GET", f"/feature-sets/{segment(feature_set_id)}")


async def export_feature_set(
    feature_set_id: str, *, part: str = "all", fmt: str = "csv"
) -> Any:
    """The modeling handoff: features plus the cohort label joined at read time."""
    return await request(
        "GET",
        f"/feature-sets/{segment(feature_set_id)}/export",
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
        f"/model-results/run/{segment(require_run())}/{segment(comparison_key)}",
        allow_404=True,
    )


async def record_model_comparison(comparison_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Every variant of one comparison, in one transaction (insert-or-read)."""
    return await request(
        "PUT",
        f"/model-results/run/{segment(require_run())}/{segment(comparison_key)}",
        json=payload,
        timeout=settings.domain_api_long_timeout(),
    )


async def get_model_result(model_result_id: str) -> dict[str, Any]:
    return await request("GET", f"/model-results/{segment(model_result_id)}")


async def session_model_results(for_session: str | None = None) -> list[dict[str, Any]]:
    return await request(
        "GET", f"/model-results/session/{segment(for_session or require_session())}"
    ) or []


async def model_results_summary(for_session: str | None = None) -> list[dict[str, Any]]:
    return await request(
        "GET", f"/model-results/session/{segment(for_session or require_session())}/summary"
    )


# ----------------------------------------------------------------------
# Literature and reports
# ----------------------------------------------------------------------
async def latest_literature_review(for_session: str | None = None) -> dict[str, Any] | None:
    """This session's most recent literature review, with its papers — the
    durable replacement for the retired workspace `literature_context.json`.
    `None` if the planner never ran the literature review phase."""
    summaries = await request(
        "GET", f"/literature-reviews/session/{segment(for_session or require_session())}"
    )
    if not summaries:
        return None
    latest = max(summaries, key=lambda item: item["created_at"])
    return await request("GET", f"/literature-reviews/{segment(latest['literature_review_id'])}")


# ----------------------------------------------------------------------
# Artifacts (step G) — retained figure and CSV bytes
# ----------------------------------------------------------------------
async def list_session_artifacts(kind: str | None = None) -> list[dict[str, Any]]:
    """This session's artifact catalogue (no bytes — `artifact_content` fetches
    those one at a time). `kind` restricts to one artifact kind, e.g.
    `figure_png`; omit it to list every kind."""
    params = {"kind": kind} if kind else None
    return await request(
        "GET",
        f"/files/session/{segment(require_session())}",
        params=params,
        timeout=settings.domain_api_long_timeout(),
    )


async def artifact_content(file_id: str) -> bytes:
    """Retained bytes, e.g. a model's per-case predictions."""
    path = f"/files/{segment(file_id)}/content"
    response = await send("GET", path, timeout=settings.domain_api_long_timeout())
    raise_for_status(response, "GET", path)
    return response.content


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
