"""``read_literature_context``: the literature review this session's planner
recorded (``literature_reviews`` + ``literature_papers``; PostgreSQL is the
only copy, there is no workspace file any more)."""

import json

from .._toolkit import tool
from ..common import domain_api
from ..common.emit import emit
from ..common.manifests import READ_ONLY


@tool(
    name="read_literature_context",
    visibility={"agent"},
    idempotent=True,
    timeout_s=45,
    **READ_ONLY,
)
async def read_literature_context() -> str | dict:
    """Read the literature review recorded during the planning phase.

    This contains the full knowledge base built from PubMed, MeSH, Wikipedia,
    Semantic Scholar, and Europe PMC — plus a curated sources guide and the
    papers found, for citations.

    Use this data for:
    - Writing the Related Work / Literature Review section
    - Adding proper references and citations
    - Comparing study results against published benchmarks
    - Providing clinical context in the Introduction

    Returns JSON with 'knowledge_base', 'sources_guide', 'pico', and 'papers' fields.
    """
    emit("tool_log", "Reading literature context.")
    try:
        review = await domain_api.latest_literature_review()
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc, "The literature context could not be read")

    if review is None:
        emit("tool_log", "No literature review is recorded for this session.")
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
