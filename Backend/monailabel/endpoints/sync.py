from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from core.database import get_db
from crud.orthanc_sync import sync_orthanc_to_db

router = APIRouter(prefix="/sync", tags=["orthanc"])

@router.post("/orthanc")
async def sync_orthanc_metadata(db: AsyncSession = Depends(get_db)):
    await sync_orthanc_to_db(db)
    return {"message": "Orthanc metadata (patients, studies, volumes, segmentations) synced successfully."}
