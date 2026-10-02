"""Compile LaTeX report source to PDF."""

import asyncio
import json
import os
from typing import Annotated

import httpx
from pydantic import Field

from .._toolkit import tool
from ..common.manifests import WRITES
from ..common.emit import emit
from ..common.http_client import shared_client, timeout, write_path_headers
from ..common.service_urls import app_api_url

PUBLISH_TIMEOUT_S = 90.0


@tool(
    visibility={"agent"},
    **WRITES,
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
                "The desired PDF filename (basename only, e.g. 'report.pdf'). "
                "Used for the download Content-Disposition header."
            )
        ),
    ],
    report_id: Annotated[
        str,
        Field(
            description=(
                "The UUID of the ``reports`` row to publish to.  Create one first "
                "via POST /reports/ with your run_id and initial source_text."
            )
        ),
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
                "the LaTeX source.  Each key must be registered via "
                "POST /reports/{report_id}/images before calling this tool."
            )
        ),
    ] = [],
) -> str:
    """Compile LaTeX and publish to the reports table."""
    emit("tool_log", "Publishing report…")

    api_base = app_api_url()
    publish_url = f"{api_base}/reports/{report_id}/publish"
    payload = {
        "latex_text": latex_text,
        "expected_version": expected_version,
        "image_keys": image_keys,
    }

    try:
        emit("tool_progress", "Compiling LaTeX…")
        resp = await shared_client().post(
            publish_url,
            json=payload,
            headers=write_path_headers(),
            timeout=timeout(PUBLISH_TIMEOUT_S),
        )

        if resp.status_code == 200:
            # Success: PDF bytes returned as application/pdf.
            pdf_size = len(resp.content)
            # Build a stable download URL via the agents file server.
            agents_base = os.environ.get("AGENTS_BASE_URL", "").rstrip("/")
            download_url = (
                f"{agents_base}/reports/{report_id}/download"
                if agents_base
                else f"{api_base}/reports/{report_id}/download"
            )
            emit("tool_log", "PDF published successfully.")
            return json.dumps(
                {
                    "status": "success",
                    "message": "Report published successfully (A19); PDF streamed, no file written.",
                    "report_id": report_id,
                    "new_version": expected_version + 1,
                    "pdf_size_bytes": pdf_size,
                    "download_url": download_url,
                }
            )

        # Non-200 — surface the error to the agent. FastAPI wraps every
        # HTTPException body as {"detail": <value>}; unwrap it once here so
        # every branch below can read `code`/`message`/etc. directly instead
        # of silently falling through to their defaults.
        try:
            body = resp.json()
            detail = body.get("detail", body) if isinstance(body, dict) else body
        except Exception:
            detail = resp.text[:2000]
        emit("error", "Report publication failed.")

        if resp.status_code == 409:
            code = detail.get("code", "conflict") if isinstance(detail, dict) else "conflict"
            return json.dumps(
                {
                    "status": "error",
                    "code": code,
                    "message": (
                        detail.get("message", str(detail))
                        if isinstance(detail, dict)
                        else str(detail)
                    ),
                    "current_version": (
                        detail.get("current_version") if isinstance(detail, dict) else None
                    ),
                }
            )

        if resp.status_code == 422:
            stdout = ""
            stderr = ""
            if isinstance(detail, dict):
                stdout = detail.get("stdout", "")
                stderr = detail.get("stderr", "")
            return json.dumps(
                {
                    "status": "error",
                    "code": "latex_compile_error",
                    "message": (
                        "LaTeX compilation failed.  The report source is unchanged (A19). "
                        "Fix the LaTeX errors below and retry with the same expected_version."
                    ),
                    "stdout": stdout,
                    "stderr": stderr,
                }
            )

        if resp.status_code == 503:
            code = detail.get("code", "unavailable") if isinstance(detail, dict) else "unavailable"
            return json.dumps(
                {
                    "status": "error",
                    "code": code,
                    "message": (
                        detail.get("message", str(detail))
                        if isinstance(detail, dict)
                        else str(detail)
                    ),
                }
            )

        return json.dumps(
            {
                "status": "error",
                "code": f"http_{resp.status_code}",
                "message": str(detail)[:2000],
            }
        )

    except httpx.TimeoutException:
        return json.dumps(
            {
                "status": "error",
                "code": "timeout",
                "message": (
                    "The publish request timed out.  The compilation may still be "
                    "running; check the report source_version before retrying."
                ),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"status": "error", "message": str(exc)})


if __name__ == "__main__":
    # Minimal smoke-test (requires a running Application API and a known report_id).
    result = asyncio.run(
        latex_to_pdf(
            latex_text=(
                r"\documentclass{article}"
                r"\begin{document}Hello, world!\end{document}"
            ),
            output_filename="test.pdf",
            report_id="00000000-0000-0000-0000-000000000000",
            expected_version=1,
        )
    )
    print(result)
