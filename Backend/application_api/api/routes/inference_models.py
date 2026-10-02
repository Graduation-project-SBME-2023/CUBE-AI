from application_api.services.monai_sync import sync_models_from_monai
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from application_api.schemas.inference_models import (
    InferenceModel,
    InferenceModelCreate,
    InferenceModelUpdate,
)
from application_api.repositories.inference_models import (
    create_model,
    delete_model,
    get_model,
    get_models,
    update_model,
)
from application_api.db.session import get_db
from fastapi import HTTPException
router = APIRouter(prefix="/inference-models", tags=["Inference models"])
from uuid import UUID
@router.get("/{inference_model_id}", response_model=InferenceModel)
async def read_model(inference_model_id: UUID, db: AsyncSession = Depends(get_db)):
    db_model = await get_model(db, inference_model_id=inference_model_id)
    return db_model

@router.get("/", response_model=list[InferenceModel])
async def read_models(skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100),
                      db: AsyncSession = Depends(get_db)):
    models = await get_models(db, skip=skip, limit=limit)
    return models

@router.post("/", response_model=InferenceModel)
async def create_model_endpoint(model: InferenceModelCreate,
                                db: AsyncSession = Depends(get_db)):
    return await create_model(db=db, model=model)

@router.put("/{inference_model_id}", response_model=InferenceModel)
async def update_model_endpoint(
    inference_model_id: UUID,
    updates: InferenceModelUpdate,
    db: AsyncSession = Depends(get_db)
):
    updated_model = await update_model(
        db,
        inference_model_id=inference_model_id,
        updates=updates.model_dump(exclude_unset=True)
    )
    if not updated_model:
        raise HTTPException(status_code=404, detail=f"Inference model {inference_model_id} not found")
    return updated_model



@router.delete("/{inference_model_id}", response_model=dict)
async def delete_model_endpoint(inference_model_id: UUID,
                                db: AsyncSession = Depends(get_db)):
    db_model = await delete_model(db, inference_model_id=inference_model_id)
    return {"detail": f"Inference model {inference_model_id} deleted successfully"}




@router.post("/sync", response_model=dict)
async def sync_models(db: AsyncSession = Depends(get_db)):
    """
    Manually trigger a sync from the MONAILabel server.
    Call this whenever you deploy new models — not on startup.
    """
    try:
        result = await sync_models_from_monai(db)
        return {"detail": "Sync complete", **result}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Sync failed: {str(e)}")
