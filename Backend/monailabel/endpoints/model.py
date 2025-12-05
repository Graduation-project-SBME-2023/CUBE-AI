from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from schemas.model import Model, ModelCreate, ModelUpdate
from crud.model import delete_model, get_model, get_models, create_model, update_model
from core.database import get_db
from fastapi import HTTPException
router = APIRouter(prefix="/models", tags=["models"])

@router.get("/{model_id}", response_model=Model)
async def read_model(model_id: int, db: Session = Depends(get_db)):
    db_model = await get_model(db, model_id=model_id)
    return db_model

@router.get("/", response_model=list[Model])
async def read_models(skip: int = 0, limit: int = 100,
                      db: Session = Depends(get_db)):
    models = await get_models(db, skip=skip, limit=limit)
    return models

@router.post("/", response_model=Model)
async def create_model_endpoint(model: ModelCreate,
                                db: Session = Depends(get_db)):
    return await create_model(db=db, model=model)

@router.put("/{model_id}", response_model=Model)
async def update_model_endpoint(
    model_id: int,
    updates: ModelUpdate,
    db: Session = Depends(get_db)
):
    updated_model = await update_model(
        db,
        model_id=model_id,
        updates=updates.dict(exclude_unset=True)
    )
    if not updated_model:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found")
    return updated_model



@router.delete("/{model_id}", response_model=dict)
async def delete_model_endpoint(model_id: int,
                                db: Session = Depends(get_db)):
    db_model = await delete_model(db, model_id=model_id)
    return {"detail": f"Model {model_id} deleted successfully"}