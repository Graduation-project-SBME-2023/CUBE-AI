from fastapi import APIRouter, Query
from monailabel.interfaces.utils.app import app_instance
import logging

logging.basicConfig(level=logging.INFO)

router = APIRouter(
    prefix="/create-model",
    tags=["Create Model"],
    responses={404: {"description": "Not found"}},
)


@router.post("/", summary="create model")
async def create_model(
        model_name: str = Query(..., description="Model name"),
        labels: str = Query(..., description="Labels in JSON format"),
        fold: int = Query(..., description="fold number"),
        plan_type: str = Query(..., description="plan type"),

):
    logging.info(f"Received request to create model: {model_name} with labels: {labels}, fold: {fold}, plan_type: {plan_type}")
    logging.info("making app instance")
    instance = app_instance()
    logging.info("app instance made")
    logging.info("creating task")
    message = instance.create_task(model_name, labels, fold, plan_type)
    logging.info("task created")
    logging.info(f"Model creation message: {message}")
    return {"message": message}