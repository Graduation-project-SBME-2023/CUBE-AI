from fastapi import APIRouter, Query
from monailabel.interfaces.utils.app import app_instance

router = APIRouter(
    prefix="/test-initialize-inference-task",
    tags=["Test Initialize Inference Task"],
    responses={404: {"description": "Not found"}},
)


@router.post("/", summary="initialize inference task")
async def initialize_inference_task(
        model_name: str = Query(..., description="Model name")
):
    instance = app_instance()

    message = instance.initialize_inference_task(model_name)
    return {"message": message}
