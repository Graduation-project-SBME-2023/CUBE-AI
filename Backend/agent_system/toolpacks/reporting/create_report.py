"""Create the `reports` row a manuscript PDF publishes to.

`latex_to_pdf` (and the underlying `POST /reports/{report_id}/publish`) needs an
existing `report_id` — its own docstring says "Create one first via POST /reports/
with your run_id and initial source_text" — but no MCP tool ever did that for
the latex_reporter_agent. Every run invented a `report_id` (a literal string or a
random UUID) and `latex_to_pdf` failed with 404 "Report not found", so no manuscript
was ever published. This tool is that missing first step.
"""

import json
from typing import Annotated, Optional

from pydantic import Field

from .. import mcp
from ..helpers import domain_api


@mcp.tool(
    name="create_report",
    annotations={
        "title": "Create the Report Row for the Manuscript PDF",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def create_report(
    source_text: Annotated[
        str,
        Field(
            description=(
                "The initial LaTeX source text (the filled template before "
                "compilation). This becomes the report's version-1 content; "
                "latex_to_pdf republishes over it with expected_version."
            )
        ),
    ],
    title: Annotated[
        Optional[str],
        Field(description="The manuscript title, if known yet."),
    ] = None,
) -> str:
    """Insert-or-read this agent run's report row. Call once, before latex_to_pdf;
    calling it again for the same run returns the same row (R13 idempotency)."""
    try:
        run_id = domain_api.require_run()
        session = domain_api.require_session()
    except domain_api.DomainApiError as exc:
        return json.dumps({"status": "error", "error_message": str(exc)}, indent=2)

    try:
        report = await domain_api.request(
            "POST",
            "/reports/",
            json={
                "session_id": session,
                "created_by_run_id": run_id,
                "source_text": source_text,
                "source_format": "latex",
                "title": title,
            },
        )
    except domain_api.DomainApiError as exc:
        return json.dumps({
            "status": "error",
            "error_message": f"Could not create the report row: {exc}",
        }, indent=2)

    return json.dumps({
        "status": "ok",
        "report_id": report["report_id"],
        "expected_version": report["source_version"],
        "next_step": (
            "Call latex_to_pdf with this report_id and expected_version to compile "
            "and publish the PDF."
        ),
    }, indent=2)
