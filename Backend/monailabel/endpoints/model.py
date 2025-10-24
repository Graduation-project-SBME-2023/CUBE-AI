from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from schemas.model import Model, ModelCreate
from crud.model import get_model, get_models, create_model
from core.database import get_db

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
