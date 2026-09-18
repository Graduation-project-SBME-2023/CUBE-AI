"""Literature repository"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.literature import LiteraturePaper, LiteratureReview
from application_api.schemas import literature as schemas
from application_api.services.publication import lock_session, publication

logger = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def _review_out(review: LiteratureReview, papers: list[LiteraturePaper]) -> dict[str, Any]:
    return {
        "literature_review_id": review.literature_review_id,
        "session_id": review.session_id,
        "review_key": review.review_key,
        "query": review.query,
        "result_summary": review.result_summary,
        "schema_version": review.schema_version,
        "created_at": review.created_at,
        "papers": [_paper_out(p) for p in papers],
    }


def _paper_out(paper: LiteraturePaper) -> dict[str, Any]:
    return {
        "paper_id": paper.paper_id,
        "literature_review_id": paper.literature_review_id,
        "pmid": paper.pmid,
        "doi": paper.doi,
        "title": paper.title,
        "authors": paper.authors,
        "year": paper.year,
        "abstract": paper.abstract,
        "journal": paper.journal,
        "relevance_score": float(paper.relevance_score) if paper.relevance_score is not None else None,
        "tier": paper.tier,
        "category": paper.category,
        "topic": paper.topic,
        "similar_study": paper.similar_study,
        "citation_metadata": paper.citation_metadata,
    }


async def _load_papers(db: AsyncSession, review_id: UUID) -> list[LiteraturePaper]:
    result = await db.execute(
        select(LiteraturePaper)
        .where(LiteraturePaper.literature_review_id == review_id)
        .order_by(LiteraturePaper.paper_id)
    )
    return list(result.scalars().all())


# ── reads ─────────────────────────────────────────────────────────────────────

async def get_review(db: AsyncSession, review_id: UUID) -> dict[str, Any]:
    review = (
        await db.execute(
            select(LiteratureReview).where(
                LiteratureReview.literature_review_id == review_id
            )
        )
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail=f"Literature review {review_id} not found")
    papers = await _load_papers(db, review_id)
    return _review_out(review, papers)


async def list_session_reviews(
    db: AsyncSession, session_id: UUID
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(LiteratureReview)
        .where(LiteratureReview.session_id == session_id)
        .order_by(LiteratureReview.created_at)
    )
    return [
        {
            "literature_review_id": r.literature_review_id,
            "session_id": r.session_id,
            "review_key": r.review_key,
            "schema_version": r.schema_version,
            "created_at": r.created_at,
        }
        for r in result.scalars().all()
    ]


# ── create review (insert-or-read) ────────────────────────────────────────────

async def create_review(
    db: AsyncSession, data: schemas.LiteratureReviewCreate
) -> tuple[dict[str, Any], bool]:
    """Create a review, or read an existing one with the same key (insert-or-read)."""
    if not data.review_key.strip():
        raise HTTPException(status_code=422, detail="review_key must not be blank")

    # Check whether it already exists before locking.
    existing = (
        await db.execute(
            select(LiteratureReview).where(
                LiteratureReview.session_id == data.session_id,
                LiteratureReview.review_key == data.review_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        papers = await _load_papers(db, existing.literature_review_id)
        return _review_out(existing, papers), False

    review = LiteratureReview(
        session_id=data.session_id,
        review_key=data.review_key,
        query=data.query,
        result_summary=data.result_summary,
    )
    db.add(review)
    try:
        async with publication(db, data.session_id) as publish:
            await db.flush()
            publish(
                "literature_review_created",
                {
                    "schema_version": 1,
                    "literature_review_id": str(review.literature_review_id),
                    "review_key": review.review_key,
                    "session_id": str(review.session_id),
                },
            )
    except IntegrityError:
        # Concurrent insert on the same key — read the winner.
        await db.rollback()
        existing = (
            await db.execute(
                select(LiteratureReview).where(
                    LiteratureReview.session_id == data.session_id,
                    LiteratureReview.review_key == data.review_key,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            raise HTTPException(status_code=500, detail="Concurrent review insert failed")
        papers = await _load_papers(db, existing.literature_review_id)
        return _review_out(existing, papers), False

    papers = await _load_papers(db, review.literature_review_id)
    return _review_out(review, papers), True


# ── add / deduplicate papers ──────────────────────────────────────────────────

async def add_papers(
    db: AsyncSession,
    review_id: UUID,
    papers: list[schemas.LiteraturePaperIn],
) -> dict[str, Any]:
    """Batch-upsert papers into a review."""
    review = (
        await db.execute(
            select(LiteratureReview).where(
                LiteratureReview.literature_review_id == review_id
            )
        )
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail=f"Literature review {review_id} not found")

    # Load existing papers indexed by pmid and doi.
    existing_papers = await _load_papers(db, review_id)
    by_pmid: dict[str, LiteraturePaper] = {
        p.pmid: p for p in existing_papers if p.pmid
    }
    by_doi: dict[str, LiteraturePaper] = {
        p.doi: p for p in existing_papers if p.doi
    }

    _MERGE_FIELDS = (
        "authors", "year", "abstract", "journal",
        "relevance_score", "tier", "category", "topic",
        "similar_study", "citation_metadata",
    )

    inserted = 0
    merged = 0

    for paper_in in papers:
        existing: LiteraturePaper | None = None
        # Prefer PMID match.
        if paper_in.pmid and paper_in.pmid in by_pmid:
            existing = by_pmid[paper_in.pmid]
        elif paper_in.doi and paper_in.doi in by_doi:
            existing = by_doi[paper_in.doi]

        if existing is not None:
            # Merge additional fields that the incoming record may have.
            changed = False
            for field in _MERGE_FIELDS:
                incoming_val = getattr(paper_in, field)
                if incoming_val is not None and getattr(existing, field) is None:
                    setattr(existing, field, incoming_val)
                    changed = True
            # If the incoming paper has the other identifier, set it.
            if paper_in.pmid and existing.pmid is None:
                existing.pmid = paper_in.pmid
                by_pmid[paper_in.pmid] = existing
                changed = True
            if paper_in.doi and existing.doi is None:
                existing.doi = paper_in.doi
                by_doi[paper_in.doi] = existing
                changed = True
            if changed:
                merged += 1
        else:
            new_paper = LiteraturePaper(
                literature_review_id=review_id,
                pmid=paper_in.pmid,
                doi=paper_in.doi,
                title=paper_in.title,
                authors=paper_in.authors,
                year=paper_in.year,
                abstract=paper_in.abstract,
                journal=paper_in.journal,
                relevance_score=paper_in.relevance_score,
                tier=paper_in.tier,
                category=paper_in.category,
                topic=paper_in.topic,
                similar_study=paper_in.similar_study,
                citation_metadata=paper_in.citation_metadata,
            )
            db.add(new_paper)
            if paper_in.pmid:
                by_pmid[paper_in.pmid] = new_paper
            if paper_in.doi:
                by_doi[paper_in.doi] = new_paper
            inserted += 1

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Paper insert rejected: {exc.orig}"
        ) from exc

    logger.info(
        "Literature review %s: %d paper(s) inserted, %d merged",
        review_id, inserted, merged,
    )
    updated_papers = await _load_papers(db, review_id)
    return {
        "literature_review_id": review_id,
        "inserted": inserted,
        "merged": merged,
        "paper_count": len(updated_papers),
        "papers": [_paper_out(p) for p in updated_papers],
    }
