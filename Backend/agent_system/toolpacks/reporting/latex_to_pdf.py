"""``latex_to_pdf``: compile a report's LaTeX and publish the PDF.

The Application API compiles and publishes in one transaction
(``POST /reports/{report_id}/publish``): the report's source becomes the new
version only when the compile succeeds, and the PDF is streamed back, never
written to the workspace.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

import httpx
from pydantic import Field

from .._toolkit import ExtractedArtifact, PortSpec, error_result, tool
from ..common import domain_api, views
from ..common.emit import emit
from ..common.manifests import WRITES
from ..common.service_urls import agents_base_url, app_api_url

PUBLISH_TIMEOUT_S = 90.0
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]*$")


def _error(code: object, message: str, **extra: Any) -> dict[str, Any]:
    """The API's own code when it is a valid error code, else ``publish_failed``."""
    code = str(code).lower()
    return error_result(
        code if _ERROR_CODE.match(code) else "publish_failed",
        message or "Report publication failed.",
        **extra,
    )


def _published_report(result: object) -> ExtractedArtifact:
    """The report the publish produced (the ``report`` output port)."""
    data = json.loads(result) if isinstance(result, str) else result
    return ExtractedArtifact(
        ref={"report_id": data["report_id"]},
        summary={"source_version": data["new_version"], "has_pdf": True},
    )


def _detail(response: httpx.Response) -> Any:
    """FastAPI wraps every HTTPException body as ``{"detail": ...}``; unwrap it once."""
    body = domain_api.response_body(response)
    return body.get("detail", body) if isinstance(body, dict) else body


def _field(detail: Any, name: str, default: Any = None) -> Any:
    return detail.get(name, default) if isinstance(detail, dict) else default


def _publish_failure(response: httpx.Response) -> dict[str, Any]:
    """The error result of a publish the API refused."""
    status = response.status_code
    if status >= 500 and status != 503:
        # A 5xx body is an unhandled server exception: never shown to the model.
        return _error("upstream_error", f"The Application API failed to publish the report ({status}).")
    detail = _detail(response)
    message = _field(detail, "message", str(detail)[:2000])
    if status == 409:
        return _error(
            _field(detail, "code", "conflict"), message, current_version=_field(detail, "current_version")
        )
    if status == 422:
        return _error(
            "latex_compile_error",
            "LaTeX compilation failed.  The report source is unchanged (A19). "
            "Fix the LaTeX errors below and retry with the same expected_version.",
            stdout=_field(detail, "stdout", ""),
            stderr=_field(detail, "stderr", ""),
        )
    if status == 503:
        return _error(_field(detail, "code", "unavailable"), message)
    return _error(f"http_{status}", str(detail)[:2000])


def _download_url(report_id: str) -> str:
    """The PDF's download route: through the agents' file server when it is
    published, else the Application API's own."""
    base = agents_base_url() or app_api_url()
    return f"{base}/reports/{report_id}/download"


@tool(
    visibility={"agent", "graph"},
    inputs={"report": PortSpec("ReportDocument", param="report_id")},
    outputs={"report": PortSpec("ReportDocument", extract=_published_report)},
    data_scopes={"db_write"},
    timeout_s=PUBLISH_TIMEOUT_S + 30,
    **WRITES,
    on_result=views.on_latex_pdf,
)
async def latex_to_pdf(
    latex_text: Annotated[
        str,
        Field(
            description=(
                "The complete LaTeX source code content, including the preamble "
                "and document body, to be compiled."
            )
        ),
    ],
    output_filename: Annotated[
        str,
        Field(
            description=(
                "A file name for the PDF (basename only, e.g. 'report.pdf'). The PDF is "
                "stored and downloaded by its report_id; this name is not used."
            )
        ),
    ],
    report_id: Annotated[
        str,
        Field(description="The report_id returned by create_report."),
    ],
    expected_version: Annotated[
        int,
        Field(
            description=(
                "The ``source_version`` of the report row you read.  Used for "
                "optimistic-concurrency: a stale version returns 409."
            ),
            ge=1,
        ),
    ] = 1,
    image_keys: Annotated[
        list[str],
        Field(
            description=(
                "The list of image_key values referenced by \\\\includegraphics in "
                "the LaTeX source.  Each key must be registered with "
                "register_report_image before calling this tool."
            )
        ),
    ] = [],
) -> str | dict[str, Any]:
    """Compile LaTeX and publish to the reports table."""
    emit("tool_log", "Publishing report…")
    emit("tool_progress", "Compiling LaTeX…")
    try:
        response = await domain_api.send(
            "POST",
            f"/reports/{domain_api.segment(report_id)}/publish",
            json={"latex_text": latex_text, "expected_version": expected_version, "image_keys": image_keys},
            timeout=PUBLISH_TIMEOUT_S,
        )
    except domain_api.DomainApiError as exc:
        if isinstance(exc.__cause__, httpx.TimeoutException):
            return _error(
                "timeout",
                "The publish request timed out.  The compilation may still be "
                "running; check the report source_version before retrying.",
            )
        return _error("unavailable", str(exc))

    if response.status_code != 200:
        return _publish_failure(response)

    emit("tool_log", "PDF published successfully.")
    return json.dumps({
        "status": "success",
        "message": "Report published successfully (A19); PDF streamed, no file written.",
        "report_id": report_id,
        "new_version": expected_version + 1,
        "pdf_size_bytes": len(response.content),
        "download_url": _download_url(report_id),
    })
