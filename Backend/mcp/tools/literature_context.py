"""
Literature Context MCP Tool

Reads the literature review this session's planner persisted through the
Application API (`literature_reviews` + `literature_papers` — Postgres is
the only copy; there is no workspace file any more).
"""

import json

from . import mcp
from tool_logging import ToolEvent, log_tool_state, TextPayload
from .helpers import domain_api


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="read_literature_context",
    annotations={
        "title": "Read Literature Review Context",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def read_literature_context() -> str:
    """Read the literature review recorded during the planning phase.

    This contains the full knowledge base built from PubMed, MeSH, Wikipedia,
    Semantic Scholar, and Europe PMC — plus a curated sources guide and the
    papers found, for citations.

    Use this data for:
    - Writing the Related Work / Literature Review section
    - Adding proper references and citations
    - Comparing study results against published benchmarks
    - Providing clinical context in the Introduction

    Returns:
        str: JSON with 'knowledge_base', 'sources_guide', 'pico', and 'papers' fields.
    """
    payload = TextPayload(message="Reading literature context.")
    log_tool_state(ToolEvent("tool_started", "text", payload))

    try:
        review = await domain_api.latest_literature_review()
    except domain_api.DomainApiError as exc:
        log_tool_state(ToolEvent("error", "text", TextPayload(message="Failed to read literature context.")))
        return json.dumps({"status": "error", "message": str(exc)}, indent=2)

    if review is None:
        log_tool_state(ToolEvent("error", "text", TextPayload(message="Literature Review Context not found.")))
        return json.dumps({
            "status": "not_available",
            "message": "No literature review found. The planner may not have run the literature review phase.",
        }, indent=2)

    log_tool_state(ToolEvent("tool_finished", "text", TextPayload(message="Literature context successfully retrieved.")))
    summary = review.get("result_summary") or {}
    return json.dumps({
        "status": "ok",
        "knowledge_base": summary.get("knowledge_base", ""),
        "sources_guide": summary.get("sources_guide", ""),
        "pico": review.get("query", {}),
        "papers": review.get("papers", []),
    }, indent=2)
