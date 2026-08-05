import asyncio
import logging
from io import BytesIO

import httpx
import imageio.v2 as imageio # Using v2 to avoid deprecation warnings
from fastapi import APIRouter, Depends, HTTPException, Query
from schemas.study import StudyRead
from sqlalchemy.ext.asyncio import AsyncSession
from schemas.volume import Volume, VolumeCreate, VolumeUpdate
from crud.volume import (
    count_volumes_by_study,
    create_volume,
    delete_volume,
    get_study_by_volume,
    get_unique_series_descriptions,
    get_volume,
    get_volumes,
    get_volumes_by_study,
    search_volumes,
    search_volumes_by_description,
    update_volume,
)
from core.config import settings
from core.database import get_db
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/volumes", tags=["volumes"])
logger = logging.getLogger(__name__)

ORTHANC_URL = settings.ORTHANC_URL.rstrip("/")
ORTHANC_AUTH = (settings.ORTHANC_USER, settings.ORTHANC_PASSWORD)
ORTHANC_PREVIEW_TIMEOUT = 60.0

MAX_CONCURRENT_GIFS = asyncio.Semaphore(5)

def create_gif_in_thread(responses):
    """
    CPU-bound task: reads JPEG bytes and encodes the GIF.
    """
    try:
        frames = [imageio.imread(r.content) for r in responses if r.status_code == 200]
        if not frames:
            return None
        
        gif_io = BytesIO()
        # loop=0 makes it loop forever
        imageio.mimsave(gif_io, frames, format='GIF', duration=1.0, loop=0)
        gif_io.seek(0)
        return gif_io
    except Exception as e:
        logger.error("GIF encoding error: %s", e, exc_info=True)
        return None

@router.get("/{volume_series_instance_uid}/preview.gif")
async def get_volume_preview_gif(volume_series_instance_uid: str):
    async with MAX_CONCURRENT_GIFS:
        async with httpx.AsyncClient(auth=ORTHANC_AUTH, timeout=ORTHANC_PREVIEW_TIMEOUT) as client:
            try:
                # 1. TRANSLATE UID TO ORTHANC ID
                # We post the UID to lookup to get the internal Orthanc UUID
                lookup_res = await client.post(
                    f"{ORTHANC_URL}/tools/lookup", 
                    content=volume_series_instance_uid
                )
                lookup_res.raise_for_status()
                lookup_data = lookup_res.json()

                if not lookup_data or lookup_data[0]["Type"] != "Series":
                    raise HTTPException(status_code=404, detail="Series UID not found in Orthanc")
                
                orthanc_series_id = lookup_data[0]["ID"]

                # 2. FETCH INSTANCES USING THE INTERNAL ID
                res = await client.get(f"{ORTHANC_URL}/series/{orthanc_series_id}/instances")
                res.raise_for_status()
                instances = res.json()
                
                if not instances:
                    raise HTTPException(status_code=404, detail="No instances found")

                # 2. Select roughly 10 slices across the volume
                # 2. Select the desired number of slices across the volume
                num_slices = 30 # Change this number to whatever you want (e.g., 20, 30, 50)
                
                count = len(instances)
                step = max(1, count // num_slices)
                sample_ids = [instances[i]['ID'] for i in range(0, count, step)][:num_slices]
                
                # 3. Fetch previews in parallel
                tasks = [client.get(f"{ORTHANC_URL}/instances/{iid}/preview") for iid in sample_ids]
                image_responses = await asyncio.gather(*tasks)
                
                # 4. Offload CPU-heavy work (imageio) to the threadpool
                gif_buffer = await run_in_threadpool(create_gif_in_thread, image_responses)
                
                if not gif_buffer:
                    raise HTTPException(status_code=500, detail="GIF generation failed")

                return StreamingResponse(
                    gif_buffer, 
                    media_type="image/gif",
                    headers={
                        "Cache-Control": "public, max-age=3600",  # Cache for 1 hour
                        "Content-Disposition": "inline",
                        "Cross-Origin-Resource-Policy": "cross-origin"
                    }
                )
                
            except httpx.ConnectError:
                raise HTTPException(status_code=502, detail="Backend cannot reach Orthanc. Check Docker networking.")
            except httpx.HTTPStatusError as e:
                raise HTTPException(status_code=e.response.status_code, detail=f"Orthanc error: {e.response.text}")
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

@router.get("/{volume_series_instance_uid}", response_model=Volume)
async def read_volume(volume_series_instance_uid: str, db: AsyncSession = Depends(get_db)):
    db_volume = await get_volume(db, volume_series_instance_uid=volume_series_instance_uid)
    return db_volume

@router.get("/", response_model=list[Volume])
async def read_volumes(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    volumes = await get_volumes(db, skip=skip, limit=limit)
    return volumes  

@router.post("/", response_model=Volume)
async def create_volume_endpoint(volume: VolumeCreate, db: AsyncSession = Depends(get_db)):
    return await create_volume(db=db, volume=volume)

@router.put("/{volume_series_instance_uid}", response_model=Volume)
async def update_volume_endpoint(
    volume_series_instance_uid: str,
    updates: VolumeUpdate,
    db: AsyncSession = Depends(get_db)
):
    updated_volume = await update_volume(db, volume_series_instance_uid, updates)
    return updated_volume

@router.delete("/{volume_series_instance_uid}")
async def delete_volume_endpoint(volume_series_instance_uid: str, db: AsyncSession = Depends(get_db)):
    """
    Delete volume from both Orthanc and database.
    This will cascade delete all related segmentations.
    """
    success, error_message = await delete_volume(db, volume_series_instance_uid)
    
    if not success:
        if error_message == "Volume not found":
            raise HTTPException(status_code=404, detail=error_message)
        else:
            raise HTTPException(status_code=500, detail=error_message)
    
    return {"detail": "Volume deleted successfully"}

@router.get("/by_study/{study_uid}", response_model=list[Volume])
async def read_volumes_by_study(study_uid: str, db: AsyncSession = Depends(get_db)):
    volumes = await get_volumes_by_study(db, study_uid)
    return volumes

@router.get("/search/", response_model=list[Volume])
async def search_volumes_endpoint(
    modality: str | None = None,
    keyword: str | None = None,
    db: AsyncSession = Depends(get_db)
):
    return await search_volumes(db, modality, keyword)

@router.get("/count/{study_uid}")
async def count_volumes(study_uid: str, db: AsyncSession = Depends(get_db)):
    return {"study_uid": study_uid, "volume_count": await count_volumes_by_study(db, study_uid)}

@router.get("/{volume_series_instance_uid}/study", response_model=StudyRead)
async def read_study_by_volume(
    volume_series_instance_uid: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Get the Study associated with a specific volume_series_instance_uid.
    """
    return await get_study_by_volume(db, volume_series_instance_uid)

@router.get("/search/description/")
async def search_volumes_description_endpoint(
    words_to_include: list[str] = Query(
        ..., 
        description="List of words to include. Pass multiple times like: ?words_to_include=brain&words_to_include=mri"
    ),
    words_to_exclude: list[str] | None = Query(
        None, 
        description="List of words to exclude. Pass multiple times like: ?words_to_exclude=contrast"
    ),
    db: AsyncSession = Depends(get_db)
):
    """
    Search volumes by keywords in series_description and body_part_examined.
    Returns detailed volume info (uid, series_description, body_part_examined, modality).
    """
    return await search_volumes_by_description(
        db=db, 
        words_to_include=words_to_include, 
        words_to_exclude=words_to_exclude
    )
    
@router.get("/descriptions/unique/", response_model=list[str])
async def get_unique_descriptions_endpoint(
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieve a list of all unique volume series descriptions available in the database.
    Useful for populating dropdown menus or showing users available search terms.
    """
    return await get_unique_series_descriptions(db=db)