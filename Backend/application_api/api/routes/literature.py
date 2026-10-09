"""Literature routes"""

from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.api.dependencies import current_user, owned_review, owned_session
from application_api.db.session import get_db
from application_api.repositories import literature as repo
from application_api.repositories.users import require_owner
from application_api.schemas.literature import (
    LiteraturePaperIn,
    LiteratureReviewCreate,
    LiteratureReviewOut,
    LiteratureReviewSummaryOut,
)

router = APIRouter(prefix="/literature-reviews", tags=["Literature"])


@router.post("/", response_model=LiteratureReviewOut, status_code=201)
async def create_review(
    data: LiteratureReviewCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(current_user),
):
    """Create a literature review, or return the existing one (insert-or-read)."""
    await require_owner(db, data.session_id, user_id)
    review, created = await repo.create_review(db, data)
    if not created:
        response.status_code = 200
    return review


@router.get("/session/{session_id}", response_model=list[LiteratureReviewSummaryOut])
async def list_session_reviews(
    session_id: UUID, db: AsyncSession = Depends(get_db), _: UUID = Depends(owned_session)
):
    """List all literature reviews for a session (no papers in the list response)."""
    return await repo.list_session_reviews(db, session_id)


@router.get("/{review_id}", response_model=LiteratureReviewOut)
async def read_review(
    review_id: UUID, db: AsyncSession = Depends(get_db), _: UUID = Depends(owned_review)
):
    """Return a literature review with all its papers."""
    return await repo.get_review(db, review_id)


@router.post("/{review_id}/papers")
async def add_papers(
    review_id: UUID,
    papers: list[LiteraturePaperIn],
    db: AsyncSession = Depends(get_db),
    _: UUID = Depends(owned_review),
):
    """Batch-add papers to a review, deduplicating by PMID and DOI."""
    return await repo.add_papers(db, review_id, papers)
