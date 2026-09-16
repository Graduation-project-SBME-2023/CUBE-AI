"""Segmentation batch routes"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.session import get_db
from application_api.repositories import segmentation_runs as repo
from application_api.schemas.segmentation_runs import (
    PairOut,
    PairStatesUpdate,
    ReviewBegin,
    ReviewDraft,
    ReviewSubmission,
    SegmentationBatchCreate,
    SegmentationBatchOut,
)

router = APIRouter(prefix="/segmentation-runs", tags=["Segmentation Runs"])


def _event_out(event):
    if event is None:
        return None
    return {
        "event_id": event.event_id,
        "msg_type": event.msg_type,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }


@router.put("/run/{agent_run_id}/{batch_key}", response_model=SegmentationBatchOut)
async def create_batch(
    agent_run_id: UUID,
    batch_key: str,
    data: SegmentationBatchCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Insert-or-read the batch on its natural key."""
    batch, created = await repo.create_batch(db, agent_run_id, batch_key, data)
    response.status_code = 201 if created else 200
    return batch


@router.get("/run/{agent_run_id}/{batch_key}", response_model=SegmentationBatchOut)
async def read_run_batch(
    agent_run_id: UUID, batch_key: str, db: AsyncSession = Depends(get_db)
):
    run = await repo.find_batch(db, agent_run_id, batch_key)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=f"Run {agent_run_id} has no segmentation batch {batch_key!r}",
        )
    return await repo.batch_out(db, run)


@router.get("/session/{session_id}", response_model=list[SegmentationBatchOut])
async def list_session_batches(session_id: UUID, db: AsyncSession = Depends(get_db)):
    return await repo.list_session_batches(db, session_id)


@router.get("/session/{session_id}/reviewed-masks", response_model=list[PairOut])
async def read_reviewed_masks(session_id: UUID, db: AsyncSession = Depends(get_db)):
    """Accepted and modified pairs of the session's submitted reviews."""
    return await repo.reviewed_masks(db, session_id)


@router.get("/{segmentation_run_id}", response_model=SegmentationBatchOut)
async def read_batch(segmentation_run_id: UUID, db: AsyncSession = Depends(get_db)):
    return await repo.get_batch(db, segmentation_run_id)


@router.patch("/{segmentation_run_id}/pairs", response_model=SegmentationBatchOut)
async def update_pairs(
    segmentation_run_id: UUID, data: PairStatesUpdate, db: AsyncSession = Depends(get_db)
):
    """Checkpoint pair progress in place. Pairs are never deleted."""
    return await repo.update_pair_states(db, segmentation_run_id, data)


@router.put("/{segmentation_run_id}/review/draft", response_model=SegmentationBatchOut)
async def save_review_draft(
    segmentation_run_id: UUID, data: ReviewDraft, db: AsyncSession = Depends(get_db)
):
    """Commit in-progress review choices durably without finishing the review."""
    return await repo.save_draft(db, segmentation_run_id, data)


@router.post("/{segmentation_run_id}/review/begin", response_model=SegmentationBatchOut)
async def begin_review(
    segmentation_run_id: UUID, data: ReviewBegin, db: AsyncSession = Depends(get_db)
):
    """`pending -> submitting`, keyed by the review's request hash."""
    return await repo.begin_review(db, segmentation_run_id, data.request_hash)


@router.post("/{segmentation_run_id}/review/abort", response_model=SegmentationBatchOut)
async def abort_review(
    segmentation_run_id: UUID, data: ReviewBegin, db: AsyncSession = Depends(get_db)
):
    return await repo.abort_review(db, segmentation_run_id, data.request_hash)


@router.post("/{segmentation_run_id}/review/complete")
async def complete_review(
    segmentation_run_id: UUID, data: ReviewSubmission, db: AsyncSession = Depends(get_db)
):
    """The one final submission. A duplicate replays; a conflicting one is 409."""
    batch, event = await repo.complete_review(db, segmentation_run_id, data)
    return {
        "batch": SegmentationBatchOut.model_validate(batch).model_dump(mode="json"),
        "event": _event_out(event),
    }
