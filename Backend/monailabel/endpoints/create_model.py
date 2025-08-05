from fastapi import APIRouter, Query
from monailabel.interfaces.utils.app import app_instance

router = APIRouter(
    prefix="/test-create-model",
    tags=["Test Create Model"],
    responses={404: {"description": "Not found"}},
)


@router.post("/", summary="create model")
async def create_model(
        model_name: str = Query(..., description="Model name"),
        labels: str = Query(..., description="Labels in JSON format"),
        fold: int = Query(..., description="fold number"),
        plan_type: str = Query(..., description="plan type"),

):
    instance = app_instance()

    message = instance.create_task(model_name, labels, fold, plan_type)
    return {"message": message}