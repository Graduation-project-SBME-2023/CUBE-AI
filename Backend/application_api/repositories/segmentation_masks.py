import logging
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from application_api.db.models.inference_models import InferenceModel
from application_api.db.models.segmentation_masks import SegmentationMask
from application_api.db.models.volume import Volume
from application_api.schemas.segmentation_masks import (
    SegmentationMaskCreate,
    SegmentationOriginDeclaration,
    SegmentationMaskUpdate,
)
from application_api.services.orthanc_client import cleanup_orthanc_resources_after_db_delete

logger = logging.getLogger(__name__)


# Get by label UID (primary key)
async def get_segmentation(db: AsyncSession, label_uid: str) -> Optional[SegmentationMask]:
    try:
        result = await db.execute(
            select(SegmentationMask).where(SegmentationMask.label_series_instance_uid == label_uid)
        )
        seg = result.scalar_one_or_none()
        if not seg:
            raise HTTPException(status_code=404, detail=f"SegmentationMask {label_uid} not found")
        return seg
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# List all (paginated)
async def get_segmentations(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[SegmentationMask]:
    try:
        result = await db.execute(select(SegmentationMask).offset(skip).limit(limit))
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# Create (idempotent — skips if label_series_instance_uid already exists)
async def create_segmentation(db: AsyncSession, segmentation: SegmentationMaskCreate) -> SegmentationMask:
    stmt = (
        pg_insert(SegmentationMask)
        .values(**segmentation.dict())
        .on_conflict_do_nothing(index_elements=["label_series_instance_uid"])
    )
    try:
        await db.execute(stmt)
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

    # Return the row (existing or newly inserted)
    result = await db.execute(
        select(SegmentationMask).where(
            SegmentationMask.label_series_instance_uid == segmentation.label_series_instance_uid
        )
    )
    return result.scalar_one()


async def update_segmentation(
    db: AsyncSession, label_uid: str, segmentation_update: SegmentationMaskUpdate
):
    db_segmentation = await get_segmentation(db, label_uid)
    if not db_segmentation:
        raise HTTPException(status_code=404, detail=f"SegmentationMask {label_uid} not found")
    update_data = segmentation_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_segmentation, key, value)
    try:
        await db.commit()
        await db.refresh(db_segmentation)
        return db_segmentation
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail= f" Failed to update: {str(e)}")


# Delete
async def delete_segmentation(db: AsyncSession, label_uid: str):
    """Delete segmentation from both the database and Orthanc. Returns (success: bool, error_message: str | None)"""
    result = await db.execute(
        select(SegmentationMask).where(SegmentationMask.label_series_instance_uid == label_uid)
    )
    segmentation = result.scalar_one_or_none()

    if not segmentation:
        return False, "SegmentationMask not found"

    orthanc_series_id = segmentation.orthanc_series_id

    try:
        await db.delete(segmentation)
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        error_msg = f"Database error: {str(e)}"
        logger.error(error_msg)
        return False, error_msg

    await cleanup_orthanc_resources_after_db_delete(
        "series",
        [orthanc_series_id],
        context=f"DB delete of segmentation {label_uid}",
    )
    return True, None


# Get by volume UID
async def get_segmentations_by_volume(
    db: AsyncSession, volume_uid: str, skip: int = 0, limit: int = 100
) -> List[SegmentationMask]:
    """Masks of one volume, each with its final review decision."""
    from sqlalchemy import text as sql_text

    try:
        result = await db.execute(
            select(SegmentationMask)
            .where(SegmentationMask.volume_series_instance_uid == volume_uid)
            .offset(skip)
            .limit(limit)
        )
        rows = list(result.scalars().all())
        if rows:
            decisions = await db.execute(
                sql_text(
                    """
                    SELECT uid,
                           CASE WHEN bool_or(positive) THEN max(positive_decision)
                                WHEN bool_or(rejected) THEN 'rejected' END AS decision
                    FROM (
                        SELECT p.selected_segmentation_uid AS uid,
                               true AS positive, false AS rejected,
                               p.review_decision AS positive_decision
                        FROM segmentation_pairs p
                        JOIN segmentation_batches r ON r.segmentation_batch_id = p.segmentation_batch_id
                        WHERE r.review_status = 'submitted'
                          AND p.volume_series_instance_uid = :volume
                          AND p.selected_segmentation_uid IS NOT NULL
                        UNION ALL
                        SELECT p.candidate_segmentation_uid, false, true, NULL
                        FROM segmentation_pairs p
                        JOIN segmentation_batches r ON r.segmentation_batch_id = p.segmentation_batch_id
                        WHERE r.review_status = 'submitted'
                          AND p.volume_series_instance_uid = :volume
                          AND p.review_decision = 'rejected'
                          AND p.candidate_segmentation_uid IS NOT NULL
                    ) decided
                    GROUP BY uid
                    """
                ),
                {"volume": volume_uid},
            )
            by_uid = {row[0]: row[1] for row in decisions.all()}
            for row in rows:
                row.review_decision = by_uid.get(row.label_series_instance_uid)
        return rows
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# Get by model_name
async def get_segmentations_by_model(
    db: AsyncSession, model_name: str, skip: int = 0, limit: int = 100
) -> List[SegmentationMask]:
    try:
        result = await db.execute(
            select(SegmentationMask)
            .join(InferenceModel, InferenceModel.inference_model_id == SegmentationMask.inference_model_id)
            .where(InferenceModel.name == model_name)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# Get unvalidated: no producing model in the catalog (candidate for review)
async def get_unvalidated_segmentations(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[SegmentationMask]:
    try:
        result = await db.execute(
            select(SegmentationMask).where(SegmentationMask.inference_model_id.is_(None)).offset(skip).limit(limit)
        )
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# Search / filter by type or method
async def search_segmentations(
    db: AsyncSession,
    series_description: Optional[str] = None,
    generation_method: Optional[str] = None,
    skip: int = 0,
    limit: int = 100
) -> List[SegmentationMask]:

    try:
        query = select(SegmentationMask)
        if series_description:
            query = query.where(
                SegmentationMask.series_description.ilike(f"%{series_description}%")
            )
        if generation_method:
            query = query.where(
                SegmentationMask.generation_method.ilike(f"%{generation_method}%")
            )
        result = await db.execute(query.offset(skip).limit(limit))
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")



# Simple statistics: count per model name, count per origin
#
# Grouped result cardinality is bounded by the number of distinct model
# names and by `origin`'s three-value CHECK, so both groupings are now
# genuinely small. The LIMIT below stays as a defensive cap.
_STATS_GROUP_LIMIT = 1000


async def get_segmentation_stats(db: AsyncSession):
    try:
        # Count per producing model name (joined through inference_model_id — the
        # denormalised `name` column was dropped by ).
        result_model = await db.execute(
            select(
                InferenceModel.name,
                func.count(SegmentationMask.label_series_instance_uid)
            )
            .select_from(SegmentationMask)
            .outerjoin(InferenceModel, InferenceModel.inference_model_id == SegmentationMask.inference_model_id)
            .group_by(InferenceModel.name)
            .limit(_STATS_GROUP_LIMIT)
        )
        counts_by_model = [{"model_name": m, "count": c} for m, c in result_model.all()]

        result_origin = await db.execute(
            select(
                SegmentationMask.origin,
                func.count(SegmentationMask.label_series_instance_uid)
            ).group_by(SegmentationMask.origin).limit(_STATS_GROUP_LIMIT)
        )
        counts_by_origin = [{"origin": o, "count": c} for o, c in result_origin.all()]

        return {"counts_by_model": counts_by_model, "counts_by_origin": counts_by_origin}

    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# ----------------------------------------------------------------------
# Origin declaration
# ----------------------------------------------------------------------
#
# The Orthanc sync writes `origin='imported'` because it genuinely cannot
# tell an assisted mask from a pipeline mask — both emit the identical
# `dd/mm/yyyy - model - label` SeriesDescription. The producer declares its
# own origin here, immediately after it STOWs.
#
# The declaration is idempotent and upserting: it may arrive before or after
# the Lua stable-series hook has synced the row, so it creates the row when
# absent and fills it in when present. Re-declaring the same origin is a
# no-op; declaring a *different* origin over an already-declared one is 409,
# never a silent overwrite.
async def declare_segmentation_origin(
    db: AsyncSession, declaration: SegmentationOriginDeclaration
):
    inference_model_id = None
    if declaration.model_name:
        inference_model_id = (
            await db.execute(
                select(InferenceModel.inference_model_id).where(InferenceModel.name == declaration.model_name)
            )
        ).scalar_one_or_none()
        if inference_model_id is None:
            logger.warning(
                "Origin declaration for %s names model %r, which is not in the "
                "catalog; recording the mask with inference_model_id NULL rather than "
                "inventing a catalog row.",
                declaration.label_series_instance_uid,
                declaration.model_name,
            )

    existing = (
        await db.execute(
            select(SegmentationMask).where(
                SegmentationMask.label_series_instance_uid
                == declaration.label_series_instance_uid
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        # Not synced yet. The producer knows both UIDs, so the row can be
        # created here; the sync pass will fill in the DICOM-derived fields
        # and will not touch `origin`.
        volume_exists = (
            await db.execute(
                select(Volume.volume_series_instance_uid).where(
                    Volume.volume_series_instance_uid
                    == declaration.volume_series_instance_uid
                )
            )
        ).scalar_one_or_none()
        if volume_exists is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Source volume {declaration.volume_series_instance_uid} is not "
                    "in the archive yet; re-declare after it syncs."
                ),
            )
        existing = SegmentationMask(
            label_series_instance_uid=declaration.label_series_instance_uid,
            # The Orthanc series id is only known after the sync pass; it is
            # NOT NULL in the archive, so the declaration seeds it with the
            # DICOM UID and the sync pass replaces it with the real one.
            orthanc_series_id=declaration.label_series_instance_uid,
            volume_series_instance_uid=declaration.volume_series_instance_uid,
            series_description=declaration.series_description or "Unknown",
            origin=declaration.origin,
        )
        db.add(existing)
    elif existing.origin not in ("imported", declaration.origin):
        raise HTTPException(
            status_code=409,
            detail=(
                f"SegmentationMask {declaration.label_series_instance_uid} is already "
                f"declared origin={existing.origin!r}; refusing to overwrite it "
                f"with {declaration.origin!r}."
            ),
        )
    else:
        existing.origin = declaration.origin

    if existing.volume_series_instance_uid != declaration.volume_series_instance_uid:
        raise HTTPException(
            status_code=409,
            detail=(
                f"SegmentationMask {declaration.label_series_instance_uid} references "
                f"volume {existing.volume_series_instance_uid}, not "
                f"{declaration.volume_series_instance_uid}."
            ),
        )

    if declaration.is_empty_mask is not None:
        existing.is_empty_mask = declaration.is_empty_mask
    if declaration.generation_method:
        existing.generation_method = declaration.generation_method
    if declaration.series_description:
        existing.series_description = declaration.series_description
    if inference_model_id is not None:
        existing.inference_model_id = inference_model_id

    try:
        await db.commit()
        await db.refresh(existing)
        return existing
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def set_segmentation_archive_status(
    db: AsyncSession, label_uid: str, archive_status: str
) -> SegmentationMask | None:
    """Mark an archive row unavailable/quarantined instead of deleting it."""
    seg = (
        await db.execute(
            select(SegmentationMask).where(
                SegmentationMask.label_series_instance_uid == label_uid
            )
        )
    ).scalar_one_or_none()
    if seg is None:
        return None
    seg.archive_status = archive_status
    try:
        await db.commit()
        await db.refresh(seg)
        return seg
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
