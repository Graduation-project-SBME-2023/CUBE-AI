"""
Literature Context MCP Tool

Reads the literature review this session's planner persisted through the
Application API (`literature_reviews` + `literature_papers` — Postgres is
the only copy; there is no workspace file any more).
"""

import json

from .._toolkit import tool
from ..common.manifests import READ_ONLY
from ..common.emit import emit
from ..common import domain_api


# ── Tools ──────────────────────────────────────────────────────────────────────

@tool(
    name="read_literature_context",
    visibility={"agent"},
    idempotent=True,
    timeout_s=45,
    **READ_ONLY,
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
    emit("tool_log", "Reading literature context.")

    try:
        review = await domain_api.latest_literature_review()
    except domain_api.DomainApiError as exc:
        emit("error", "Failed to read literature context.")
        return json.dumps({"status": "error", "message": str(exc)}, indent=2)

    if review is None:
        emit("error", "Literature Review Context not found.")
        return json.dumps({
            "status": "not_available",
            "message": "No literature review found. The planner may not have run the literature review phase.",
        }, indent=2)

    emit("tool_log", "Literature context successfully retrieved.")
    summary = review.get("result_summary") or {}
    return json.dumps({
        "status": "ok",
        "knowledge_base": summary.get("knowledge_base", ""),
        "sources_guide": summary.get("sources_guide", ""),
        "pico": review.get("query", {}),
        "papers": review.get("papers", []),
    }, indent=2)
