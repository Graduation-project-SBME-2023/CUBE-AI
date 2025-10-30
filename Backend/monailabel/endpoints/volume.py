from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from schemas.volume import Volume, VolumeCreate, VolumeUpdate
from crud.volume import count_volumes_by_study, delete_volume, get_volume, get_volumes, create_volume, get_volumes_by_study, search_volumes, search_volumes, update_volume
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

@router.put("/{volume_series_instance_uid}", response_model=Volume)
async def update_volume_endpoint(
    volume_series_instance_uid: str,
    updates: VolumeUpdate,
    db: Session = Depends(get_db)
):
    updated_volume = await update_volume(db, volume_series_instance_uid, updates)
    return updated_volume

@router.delete("/{volume_series_instance_uid}", response_model=Volume)
async def delete_volume_endpoint(volume_series_instance_uid: str, db: Session = Depends(get_db)):
    deleted_volume = await delete_volume(db, volume_series_instance_uid)
    return deleted_volume

@router.get("/by_study/{study_uid}", response_model=list[Volume])
async def read_volumes_by_study(study_uid: str, db: Session = Depends(get_db)):
    volumes = await get_volumes_by_study(db, study_uid)
    return volumes

@router.get("/search/", response_model=list[Volume])
async def search_volumes_endpoint(
    modality: str | None = None,
    keyword: str | None = None,
    db: Session = Depends(get_db)
):
    return await search_volumes(db, modality, keyword)

@router.get("/count/{study_uid}")
async def count_volumes(study_uid: str, db: Session = Depends(get_db)):
    return {"study_uid": study_uid, "volume_count": await count_volumes_by_study(db, study_uid)}

