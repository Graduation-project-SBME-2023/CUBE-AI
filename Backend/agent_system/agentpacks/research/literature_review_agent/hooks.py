"""Hooks of ``literature_review_agent`` (see ``toolkit/agents/loader.py``).

A workflow agent (Phase 9.6): its five internal agents are the steps of the
graph ``build_graph`` returns, never ``run_agent`` delegates. The step
contracts are in ``contracts.py``.

What the graph must do around the steps, so the engine work (Phase 9.6) can
build it from here:

- **first**, read the PICO from the node's params, build the knowledge base
  for it, and call ``literature.run_state.use_pico(run_id, pico)`` so the
  search tools filter with this study's domain keywords;
- between ``search_executor`` and ``relevance_ranker``, deduplicate the
  papers across databases (``search_executor`` deduplicates only its own);
  after ``relevance_ranker``, sort by ``relevance_score`` and number the ranks;
- **last**, in one ``ctx.durable`` step: take the captured files with
  ``literature.run_state.release(run_id)`` (``contracts.REVIEW_FILES``),
  record the review (``POST /literature-reviews/`` with
  ``review_key = <run_id>``, then its papers), and return ``ReviewRecorded``.

That last step replaces the "output hook" the Plan 2 notes expected here: the
recording belongs inside the graph, where a resume does not repeat it, rather
than in the Node Runner. ``review_key = run_id`` is what lets the completion
check below tell this run's review from an earlier one.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..._runtime import ExtractedArtifact, ToolContext, domain_api


class ReviewRecorded(BaseModel):
    """The workflow's result, from its last (recording) step."""

    model_config = ConfigDict(extra="forbid")

    literature_review_id: UUID
    n_papers: int = Field(ge=0)
    highly_relevant: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)


output_contract = ReviewRecorded


def build_graph(ctx: ToolContext) -> Any:
    """The review graph. LangGraph arrives with the engine (Phase 9.1), so the
    graph is built there; the steps, their order and their contracts are
    already fixed here and in ``contracts.py``."""
    raise NotImplementedError(
        "literature_review_agent's graph is built in Phase 9.6, with the LangGraph engine"
    )


async def completion_check(ctx: ToolContext, output: ReviewRecorded | None) -> list[str]:
    """The review is recorded for this session, under this run, with the
    papers the result counts. An unreachable Application API propagates."""
    if not ctx.run_id:
        return ["this agent is not running inside a node; there is no run to record on"]
    if output is None:
        return ["the review was not recorded: the workflow returned no result"]
    review = await domain_api.request(
        "GET",
        f"/literature-reviews/{domain_api.segment(output.literature_review_id)}",
        allow_404=True,
    )
    if review is None:
        return [f"literature review {output.literature_review_id} does not exist"]
    issues = []
    if str(review.get("session_id")) != str(ctx.session_id) or review.get("review_key") != str(
        ctx.run_id
    ):
        issues.append(
            f"literature review {output.literature_review_id} was not recorded by this run "
            "(review_key must be the run id)"
        )
    papers = review.get("papers") or []
    if len(papers) != output.n_papers:
        issues.append(f"the review records {len(papers)} papers, not {output.n_papers}")
    return issues


def extract_literature(result: Any) -> ExtractedArtifact:
    """The ``LiteratureContext`` of the recorded review."""
    output = ReviewRecorded.model_validate(result.output)
    return ExtractedArtifact(
        ref={"literature_review_id": str(output.literature_review_id)},
        summary={"n_papers": output.n_papers},
    )
