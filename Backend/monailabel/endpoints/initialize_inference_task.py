from fastapi import APIRouter, Depends , Query
from monailabel.interfaces.utils.app import app_instance
from monailabel.interfaces.app import MONAILabelApp


router = APIRouter(
    prefix="/initialize-inference-task",
    tags=["Initialize Inference Task"],
    responses={404: {"description": "Not found"}},
)


@router.post("/", summary="intialize inference task")
async def initialize_inference_task(
    model_name: str = Query(..., description="Model name"),
    
):
    instance: MONAILabelApp = app_instance()

    message = instance.initialize_inference_task(model_name)
    return {"message": message}