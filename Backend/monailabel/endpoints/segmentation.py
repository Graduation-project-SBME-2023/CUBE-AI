from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from schemas.segmentation import Segmentation, SegmentationCreate
from crud.segmentation import get_segmentation, get_segmentations, create_segmentation
from core.database import get_db

router = APIRouter(prefix="/segmentations", tags=["segmentations"])

@router.get("/{segmentation_id}", response_model=Segmentation)
async def read_segmentation(segmentation_id: int, db: Session = Depends(get_db)):
    db_segmentation = await get_segmentation(db, segmentation_id=segmentation_id)
    return db_segmentation

@router.get("/", response_model=list[Segmentation])
async def read_segmentations(skip: int = 0, limit: int = 100,
                                db: Session = Depends(get_db)):
        segmentations = await get_segmentations(db, skip=skip, limit=limit)
        return segmentations

@router.post("/", response_model=Segmentation)
async def create_segmentation_endpoint(segmentation: SegmentationCreate,
                                       db: Session = Depends(get_db)):
    return await create_segmentation(db=db, segmentation=segmentation)
