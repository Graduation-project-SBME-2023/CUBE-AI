

from __future__ import annotations

import pytest

from application_api.schemas.literature import (
    LiteraturePaperIn,
    LiteratureReviewCreate,
    LiteratureReviewOut,
    LiteraturePaperOut,
)


# ── LiteratureReviewCreate validation ────────────────────────────────────────

@pytest.mark.unit
def test_review_create_requires_review_key():
    with pytest.raises(Exception):
        LiteratureReviewCreate(
            session_id="00000000-0000-0000-0000-000000000001",
            review_key="",   # blank — should fail min_length
            query={"schema_version": 1},
        )


@pytest.mark.unit
def test_review_create_valid():
    review = LiteratureReviewCreate(
        session_id="00000000-0000-0000-0000-000000000001",
        review_key="pico_v1",
        query={"schema_version": 1, "population": "adult spleen"},
        result_summary={"schema_version": 1, "context": "…"},
    )
    assert review.review_key == "pico_v1"
    assert review.result_summary["context"] == "…"


# ── LiteraturePaperIn validation ─────────────────────────────────────────────

@pytest.mark.unit
def test_paper_requires_at_least_one_identifier():
    with pytest.raises(Exception, match="pmid.*doi|doi.*pmid|identifier"):
        LiteraturePaperIn(title="Some paper")


@pytest.mark.unit
def test_paper_pmid_only():
    paper = LiteraturePaperIn(pmid="12345678", title="Spleen radiomics")
    assert paper.pmid == "12345678"
    assert paper.doi is None


@pytest.mark.unit
def test_paper_doi_only():
    paper = LiteraturePaperIn(doi="10.1234/test", title="Some paper")
    assert paper.doi == "10.1234/test"
    assert paper.pmid is None


@pytest.mark.unit
def test_paper_both_identifiers():
    paper = LiteraturePaperIn(
        pmid="12345678", doi="10.1234/test", title="Both identifiers"
    )
    assert paper.pmid == "12345678"
    assert paper.doi == "10.1234/test"


@pytest.mark.unit
def test_paper_relevance_score_bounded():
    with pytest.raises(Exception):
        LiteraturePaperIn(pmid="1", title="Bad score", relevance_score=101.0)
    with pytest.raises(Exception):
        LiteraturePaperIn(pmid="1", title="Negative score", relevance_score=-1.0)


@pytest.mark.unit
def test_paper_relevance_score_edge_values():
    for score in (0.0, 50.0, 100.0):
        paper = LiteraturePaperIn(pmid="1", title="Valid", relevance_score=score)
        assert paper.relevance_score == score


@pytest.mark.unit
def test_paper_year_positive():
    with pytest.raises(Exception):
        LiteraturePaperIn(pmid="1", title="Bad year", year=0)
    with pytest.raises(Exception):
        LiteraturePaperIn(pmid="1", title="Negative year", year=-1)


@pytest.mark.unit
def test_paper_optional_fields_are_none_by_default():
    paper = LiteraturePaperIn(pmid="1", title="Minimal")
    assert paper.authors is None
    assert paper.year is None
    assert paper.abstract is None
    assert paper.journal is None
    assert paper.tier is None
    assert paper.category is None
    assert paper.topic is None
    assert paper.similar_study is None
    assert paper.citation_metadata is None


# ──: literature_papers has no created_at ───────────────────────────────────

@pytest.mark.unit
def test_paper_schema_has_no_created_at():
    """decision: papers have no created_at — dated by the parent review."""
    fields = LiteraturePaperOut.model_fields
    assert "created_at" not in fields, (
        "literature_papers must not have created_at (O4, closed 2026-09-15)"
    )


# ── Review schema has created_at (parent row) ─────────────────────────────────

@pytest.mark.unit
def test_review_schema_has_created_at():
    fields = LiteratureReviewOut.model_fields
    assert "created_at" in fields


# ── Citation metadata survives deduplication ─────────────────────────────────
# This tests the merge logic pattern (the actual DB merging is integration-
# tested). Here we verify the schema accepts citation_metadata round-trips.

@pytest.mark.unit
def test_citation_metadata_survives_round_trip():
    citation = {
        "provider": "pubmed",
        "mesh_terms": ["Spleen", "Radiomics"],
        "impact_factor": 4.2,
    }
    paper = LiteraturePaperIn(
        pmid="12345",
        title="Citation metadata test",
        citation_metadata=citation,
    )
    assert paper.citation_metadata["provider"] == "pubmed"
    assert paper.citation_metadata["mesh_terms"] == ["Spleen", "Radiomics"]


# ── Deduplication merge logic (unit, no DB) ───────────────────────────────────

@pytest.mark.unit
def test_merge_logic_prefers_pmid_match():
    """If PMID matches, DOI should be filled in from the incoming record."""
    # Simulate the merge: existing has PMID but no DOI; incoming has both.
    class _FakePaper:
        pmid = "12345"
        doi = None
        authors = None
        year = None
        abstract = None
        journal = None
        relevance_score = None
        tier = None
        category = None
        topic = None
        similar_study = None
        citation_metadata = None

    existing = _FakePaper()
    incoming = LiteraturePaperIn(
        pmid="12345",
        doi="10.1234/spleen",
        title="Spleen study",
        authors=["Smith J"],
    )

    # Apply the same merge logic as the repository.
    _MERGE_FIELDS = (
        "authors", "year", "abstract", "journal",
        "relevance_score", "tier", "category", "topic",
        "similar_study", "citation_metadata",
    )
    for field in _MERGE_FIELDS:
        incoming_val = getattr(incoming, field)
        if incoming_val is not None and getattr(existing, field) is None:
            setattr(existing, field, incoming_val)

    if incoming.doi and existing.doi is None:
        existing.doi = incoming.doi

    assert existing.doi == "10.1234/spleen"
    assert existing.authors == ["Smith J"]
    assert existing.pmid == "12345"   # unchanged
