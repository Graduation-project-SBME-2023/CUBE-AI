"""Hooks of ``literature_review_agent`` (see ``toolkit/agents/loader.py``).

An ordinary execution agent (Phase 3.2): it searches, ranks and compiles the
review itself, or hands parts of it to its five SDK subagents (its
``delegates``), in any order. The shapes those subagents answer with are in
``contracts.py``.

- ``context_builder``: derives the run's domain keywords from the PICO in the
  task (``literature.run_state.use_pico``), so the search tools filter with
  this study's terms, and returns the first message;
- ``output_contract``: ``CompiledContext``, what the agent reports;
- ``completion_check``: the three review files are captured for this run;
- ``on_complete``: takes the files (``run_state.release``), records the review
  under ``review_key = <run_id>`` with its papers, sends ``lit_review_output``
  so ``LiteratureReviewView`` keeps working, and returns ``ReviewRecorded``;
- ``extract_literature``: the ``literature`` output port, from that record.

Both writes are insert-or-read (a review per ``review_key``, papers deduplicated
by PMID and DOI), so a retry that runs ``on_complete`` again records nothing
twice. A retry after ``release`` has no files left: the check then reports
them missing and the agent saves them again.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..._runtime import (
    ExtractedArtifact,
    ToolContext,
    ViewEvent,
    domain_api,
    literature_run_state,
)
from .contracts import REVIEW_FILES, CompiledContext

logger = logging.getLogger(__name__)

LIT_REVIEW_VIEW = "lit_review_output"


class ReviewRecorded(BaseModel):
    """The run's output once the review is recorded (``on_complete``)."""

    model_config = ConfigDict(extra="forbid")

    literature_review_id: UUID
    n_papers: int = Field(ge=0)
    highly_relevant: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)


output_contract = CompiledContext


def context_builder(task: str, inputs: Mapping[str, Any], ctx: ToolContext) -> str:
    """The task is the first message; it states the PICO the searches filter on."""
    if ctx.run_id:
        literature_run_state.use_pico(str(ctx.run_id), {"task": task})
    return task


async def completion_check(ctx: ToolContext, output: CompiledContext | None) -> list[str]:
    """Every review file is captured for this run."""
    if not ctx.run_id:
        return ["this agent is not running inside a node; there is no run to record on"]
    if output is None:
        return ["finish with the saved files and the paper counts in the required format"]
    captured = literature_run_state.captured_outputs(str(ctx.run_id))
    return [
        f"{name} is not saved: write it and capture it with save_literature_output"
        for name in REVIEW_FILES
        if not captured.get(name, "").strip()
    ]


async def on_complete(ctx: ToolContext, output: Any) -> dict[str, Any]:
    """Record the review and its papers, show it, and return ``ReviewRecorded``.
    An unreachable Application API propagates and fails the attempt."""
    compiled = CompiledContext.model_validate(output)
    run_id = str(ctx.run_id)
    files = literature_run_state.release(run_id)
    context = _json(files.get("context.json"), {})
    ranked = _json(files.get("ranked_papers.json"), [])
    papers = ranked if isinstance(ranked, list) else []

    review = await domain_api.request(
        "POST",
        "/literature-reviews/",
        json={
            "session_id": str(ctx.session_id),
            "review_key": run_id,
            "query": (context.get("metadata") or {}).get("pico") or {},
            "result_summary": {"context": context, "summary": compiled.summary},
        },
    )
    review_id = review["literature_review_id"]
    recorded = [paper for paper in map(_paper, papers) if paper is not None]
    if recorded:
        await domain_api.request(
            "POST", f"/literature-reviews/{domain_api.segment(review_id)}/papers", json=recorded
        )

    report = files.get("literature_report.md", "")
    await _show(ctx, {"report": report, "context": context, "ranked_papers": papers})
    warnings = [] if recorded else ["no paper had a PMID or a DOI, so none was recorded"]
    return ReviewRecorded(
        literature_review_id=review_id,
        n_papers=len(recorded),
        highly_relevant=compiled.highly_relevant,
        warnings=warnings,
    ).model_dump(mode="json")


def _json(text: str | None, default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except ValueError:
        logger.warning("a literature review file is not valid JSON; recorded without it")
        return default


def _paper(paper: Any) -> dict[str, Any] | None:
    """One ranked paper as ``LiteraturePaperIn``; ``None`` without a PMID or a DOI."""
    if not isinstance(paper, Mapping):
        return None
    pmid = str(paper.get("pmid") or "").strip() or None
    doi = str(paper.get("doi") or "").strip() or None
    title = str(paper.get("title") or "").strip()
    if not title or (pmid is None and doi is None):
        return None
    authors = paper.get("authors")
    names = authors if isinstance(authors, list) else str(authors or "").split(",")
    score = paper.get("relevance_score")
    return {
        "pmid": pmid,
        "doi": doi,
        "title": title,
        "authors": [str(name).strip() for name in names if str(name).strip()] or None,
        "year": _year(paper.get("year_numeric") or paper.get("year")),
        "abstract": str(paper.get("abstract") or "").strip() or None,
        "journal": str(paper.get("journal") or "").strip() or None,
        "relevance_score": float(score) if isinstance(score, (int, float)) else None,
        "tier": paper.get("tier") or None,
        "similar_study": paper.get("similar_study"),
    }


def _year(raw: Any) -> int | None:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if 0 < value < 10000 else None


async def _show(ctx: ToolContext, payload: dict[str, Any]) -> None:
    if ctx.event_sink is None:
        return
    try:
        await ctx.event_sink.emit_view(ViewEvent(LIT_REVIEW_VIEW, payload))
    except Exception:  # the review is recorded; a view that does not reach the UI is not fatal
        logger.exception("could not send %s for run %s", LIT_REVIEW_VIEW, ctx.run_id)


def extract_literature(result: Any) -> ExtractedArtifact:
    """The ``LiteratureContext`` of the recorded review."""
    output = ReviewRecorded.model_validate(result.output)
    return ExtractedArtifact(
        ref={"literature_review_id": str(output.literature_review_id)},
        summary={"n_papers": output.n_papers},
    )
