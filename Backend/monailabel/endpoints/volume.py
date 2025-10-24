from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from schemas.volume import Volume, VolumeCreate
from crud.volume import get_volume, get_volumes, create_volume
from core.database import get_db

router = APIRouter(prefix="/volumes", tags=["volumes"])

@router.get("/{volume_series_instance_uid}", response_model=Volume)
async def read_volume(volume_series_instance_uid: str, db: Session = Depends(get_db)):
    db_volume = await get_volume(db, volume_series_instance_uid=volume_series_instance_uid)
    return db_volume

@router.get("/", response_model=list[Volume])
async def read_volumes(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    volumes = await get_volumes(db, skip=skip, limit=limit)
    return volumes  

@router.post("/", response_model=Volume)
async def create_volume_endpoint(volume: VolumeCreate, db: Session = Depends(get_db)):
    return await create_volume(db=db, volume=volume)
