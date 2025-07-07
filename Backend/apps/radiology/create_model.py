from fastapi import APIRouter, Depends , Query
from monailabel.interfaces.utils.app import app_instance
from monailabel.interfaces.app import MONAILabelApp


router = APIRouter(
    prefix="/create-model",
    tags=["Create Model"],
    responses={404: {"description": "Not found"}},
)


@router.get("/", summary="create model")
async def create_model(
    class_name: str = Query(..., description="Class name"),
    model_name: str = Query(..., description="Model name"),
    model_path: str = Query(..., description="Model path"),
    labels: str = Query(..., description="Labels in JSON format"),
    task: str = Query(..., description="Task"),
    data_name: str = Query(..., description="Data name"),
    
):
    instance: MONAILabelApp = app_instance()

    message = instance.create_task(class_name, model_name, model_path, labels, task, data_name)
    return {"message": message}