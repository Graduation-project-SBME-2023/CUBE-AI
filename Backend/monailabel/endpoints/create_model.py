import logging

from fastapi import APIRouter, HTTPException, Query
from monailabel.interfaces.utils.app import app_instance

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/create-model",
    tags=["Create Model"],
    responses={404: {"description": "Not found"}},
)


@router.post("/", summary="create model")
async def create_model(
    model_name: str = Query(..., description="Model name"),
    labels: str = Query(..., description="Labels in JSON format"),
    fold: int = Query(..., ge=0, description="Fold number"),
    plan_type: str = Query(..., min_length=1, description="nnUNet plan type"),
):
    logger.info(
        "Creating dynamic model %s (fold=%s, plan=%s)",
        model_name,
        fold,
        plan_type,
    )
    instance = app_instance()
    try:
        message = instance.create_task(model_name, labels, fold, plan_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"message": message}
