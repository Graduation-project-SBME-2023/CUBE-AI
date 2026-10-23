"""What each step of the literature review hands to the next (merged plan,
Phases 3.3 and 9.6).

The review is a fixed graph::

    pico_analyst -> query_builder -> search_executor (one branch per database)
                 -> relevance_ranker (one branch per paper) -> context_compiler

Each internal agent's ``output_contract`` is one of these models, so the
engine checks a step's answer before the next step reads it, and
``hooks.build_graph`` (Phase 9.6) wires the steps with them. The steps read
plenty of free text, so the models ignore fields they do not name rather than
refuse an answer for an extra key; what the next step needs is required.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Database = Literal["pubmed", "arxiv", "semantic_scholar", "europe_pmc"]
# The search tool of each database; a search_executor branch uses one of them.
DATABASE_TOOLS: dict[str, str] = {
    "pubmed": "search_pubmed",
    "arxiv": "search_arxiv",
    "semantic_scholar": "search_semantic_scholar",
    "europe_pmc": "search_europe_pmc",
}
Tier = Literal["highly_relevant", "moderately_relevant", "low_relevance"]
JournalTier = Literal["tier1", "tier2", "specialty", "preprint", "unknown"]
# The files context_compiler captures with save_literature_output; the step
# that records the review reads them from literature.run_state.
REVIEW_FILES = ("literature_report.md", "context.json", "ranked_papers.json")
SCORE_TOLERANCE = 0.5


class _Step(BaseModel):
    model_config = ConfigDict(extra="ignore")


# --- pico_analyst


class Pico(_Step):
    population: str = Field(min_length=1)
    intervention: str = Field(min_length=1)
    comparison: str = ""
    outcome: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    question: str = ""


class Dimension(_Step):
    name: str = Field(min_length=1)
    subtopics: list[str] = Field(min_length=1)
    mesh_terms: list[str] = Field(default_factory=list)
    best_sources: list[str] = Field(default_factory=list)


class PicoAnalysis(_Step):
    pico: Pico
    clinical_context: dict[str, Any] = Field(default_factory=dict)
    dimensions: list[Dimension] = Field(min_length=1)
    seed_keywords: list[str] = Field(min_length=1)
    similar_research_targets: list[str] = Field(default_factory=list)


# --- query_builder


class SearchQuery(_Step):
    id: str = Field(min_length=1)
    query_text: str = Field(min_length=1, max_length=300)
    db: Database
    dimension: str = ""
    priority: int = Field(default=2, ge=1, le=3)
    intent: str = ""


class SimilarityQuery(_Step):
    id: str = Field(min_length=1)
    query_text: str = Field(min_length=1, max_length=300)
    db: Literal["semantic_scholar", "europe_pmc"]
    intent: str = ""


class QueryPlan(_Step):
    queries: list[SearchQuery] = Field(min_length=1)
    hypothesis_similarity_queries: list[SimilarityQuery] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> QueryPlan:
        ids = [q.id for q in self.queries] + [q.id for q in self.hypothesis_similarity_queries]
        if duplicates := sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"query ids are used more than once: {duplicates}")
        return self

    def for_database(self, db: str) -> list[SearchQuery | SimilarityQuery]:
        """The queries one search_executor branch runs, priority order first."""
        ranked = sorted((q for q in self.queries if q.db == db), key=lambda q: q.priority)
        return [*ranked, *(q for q in self.hypothesis_similarity_queries if q.db == db)]


# --- search_executor (one branch per database)


class FoundPaper(_Step):
    title: str = Field(min_length=1)
    authors: str = ""
    year: str = ""
    year_numeric: int = 0
    journal: str = ""
    journal_tier: JournalTier = "unknown"
    abstract: str = ""
    url: str = ""
    pmid: str | None = None
    doi: str | None = None
    query_id: str = Field(min_length=1)
    query_dimension: str = ""
    query_intent: str = ""
    is_hypothesis_similar: bool = False
    has_abstract: bool = False
    is_recent: bool = False


class SourceSearch(_Step):
    database: Database
    queries_executed: int = Field(ge=0)
    failed_queries: list[str] = Field(default_factory=list, description="ids of failed queries")
    papers: list[FoundPaper] = Field(default_factory=list)


# --- relevance_ranker (one branch per paper)


class ScoredPaper(_Step):
    title: str = Field(min_length=1)
    raw_score: float = Field(ge=0, le=100)
    expert_adjustment: float = Field(ge=-100, le=100)
    relevance_score: float = Field(ge=0, le=100)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    tier: Tier
    similar_study: bool = False
    key_contribution: str = ""
    methodology_notes: str = ""
    clinical_relevance: str = ""

    @model_validator(mode="after")
    def _score_adds_up(self) -> ScoredPaper:
        expected = min(max(self.raw_score + self.expert_adjustment, 0.0), 100.0)
        if abs(self.relevance_score - expected) > SCORE_TOLERANCE:
            raise ValueError(
                f"relevance_score {self.relevance_score} is not raw_score {self.raw_score} "
                f"plus expert_adjustment {self.expert_adjustment}, capped to 0-100 ({expected})"
            )
        return self


# --- context_compiler


class CompiledContext(_Step):
    files_saved: list[str]
    papers_reviewed: int = Field(ge=0)
    highly_relevant: int = Field(ge=0)
    similar_studies: int = Field(ge=0)
    summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def _every_file_saved(self) -> CompiledContext:
        if missing := [name for name in REVIEW_FILES if name not in self.files_saved]:
            raise ValueError(f"not saved: {missing}; save every file with save_literature_output")
        return self
