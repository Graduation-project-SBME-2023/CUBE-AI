from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional

from application_api.db.session import get_db
from application_api.schemas.segmentation import (
    Segmentation,
    SegmentationCreate,
    SegmentationOriginDeclaration,
    SegmentationUpdate,
)
from application_api.repositories.segmentation import (
    get_segmentation,
    get_segmentations,
    create_segmentation,
    update_segmentation,
    delete_segmentation,
    get_segmentations_by_volume,
    get_segmentations_by_model,
    get_unvalidated_segmentations,
    search_segmentations,
    get_segmentation_stats,
    declare_segmentation_origin,
)

router = APIRouter(prefix="/segmentations", tags=["Segmentations"])




# ----------------------------------------------------------------------
# List all (paginated)
# ----------------------------------------------------------------------
@router.get("/", response_model=List[Segmentation])
async def read_segmentations(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await get_segmentations(db, skip=skip, limit=limit)


# ----------------------------------------------------------------------
# Create segmentation
# ----------------------------------------------------------------------
@router.post("/", response_model=Segmentation)
async def create_segmentation_endpoint(
    segmentation: SegmentationCreate, db: AsyncSession = Depends(get_db)
):
    return await create_segmentation(db, segmentation)


# ----------------------------------------------------------------------
# Update segmentation
# ----------------------------------------------------------------------
@router.put("/{label_uid}", response_model=Segmentation)
async def update_segmentation_endpoint(
    label_uid: str,
    segmentation_update: SegmentationUpdate,
    db: AsyncSession = Depends(get_db),
):
    updated = await update_segmentation(db, label_uid, segmentation_update)
    if not updated:
        raise HTTPException(status_code=404, detail="Segmentation not found")
    return updated


# ----------------------------------------------------------------------
# Delete segmentation
# ----------------------------------------------------------------------
@router.delete("/{label_uid}", response_model=dict)
async def delete_segmentation_endpoint(label_uid: str, db: AsyncSession = Depends(get_db)):
    """
    Delete segmentation from both Orthanc and database.
    """
    success, error_message = await delete_segmentation(db, label_uid)
    
    if not success:
        if error_message == "Segmentation not found":
            raise HTTPException(status_code=404, detail=error_message)
        else:
            raise HTTPException(status_code=500, detail=error_message)
    
    return {"detail": "Segmentation deleted successfully"}


# ----------------------------------------------------------------------
# Declare how a mask was produced
# ----------------------------------------------------------------------
@router.post("/declare-origin", response_model=Segmentation)
async def declare_segmentation_origin_endpoint(
    declaration: SegmentationOriginDeclaration,
    db: AsyncSession = Depends(get_db),
):
    """Record that this mask came from the assisted or the pipeline path."""
    return await declare_segmentation_origin(db, declaration)


# ----------------------------------------------------------------------
# Get segmentations by volume UID
# ----------------------------------------------------------------------
@router.get("/by-volume/{volume_uid}", response_model=List[Segmentation])
async def read_segmentations_by_volume(
    volume_uid: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await get_segmentations_by_volume(db, volume_uid, skip=skip, limit=limit)


# ----------------------------------------------------------------------
# Get segmentations by model_name
# ----------------------------------------------------------------------
@router.get("/by-model/{model_name}", response_model=List[Segmentation])
async def read_segmentations_by_model(
    model_name: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await get_segmentations_by_model(db, model_name, skip=skip, limit=limit)


# ----------------------------------------------------------------------
# Get unvalidated (no producing model in the catalog)
# ----------------------------------------------------------------------
@router.get("/unvalidated/", response_model=List[Segmentation])
async def read_unvalidated_segmentations(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await get_unvalidated_segmentations(db, skip=skip, limit=limit)


# ----------------------------------------------------------------------
# Search / filter by series description or generation method
# ----------------------------------------------------------------------
@router.get("/search/", response_model=List[Segmentation])
async def search_segmentations_endpoint(
    series_description: Optional[str] = Query(
        None, description="Filter by DICOM SeriesDescription"
    ),
    generation_method: Optional[str] = Query(
        None, description="Filter by how the mask was generated"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await search_segmentations(
        db,
        series_description=series_description,
        generation_method=generation_method,
        skip=skip,
        limit=limit,
    )


# ----------------------------------------------------------------------
# Simple statistics
# ----------------------------------------------------------------------
@router.get("/stats/", response_model=dict)
async def get_segmentation_stats_endpoint(db: AsyncSession = Depends(get_db)):
    return await get_segmentation_stats(db)


# ----------------------------------------------------------------------
# Get by label UID (Wildcard, must be last)
# ----------------------------------------------------------------------
@router.get("/{label_uid}", response_model=Segmentation)
async def read_segmentation(label_uid: str, db: AsyncSession = Depends(get_db)):
    seg = await get_segmentation(db, label_uid)
    if not seg:
        raise HTTPException(status_code=404, detail="Segmentation not found")
    return seg
