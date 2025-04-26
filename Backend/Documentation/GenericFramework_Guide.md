# Generic Framework Documentation

### Follow the following steps before you run the backend for the first time

---

## create initialize_inference_task.py this file to endpoints in MONAILabel

```PYTHON
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
```

## create create_model.py this file to endpoints in MONAILabel

```PYTHON
from fastapi import APIRouter, Depends , Query
from monailabel.interfaces.utils.app import app_instance
from monailabel.interfaces.app import MONAILabelApp


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
    instance: MONAILabelApp = app_instance()

    message = instance.create_task(model_name, labels, fold, plan_type)
    return {"message": message}
```

## Add this lines in Lib\site-packages\monailabel\app.py

```PYTHON
app.include_router(initialize_inference_task.router, prefix=settings.MONAI_LABEL_API_STR)
app.include_router(create_model.router, prefix=settings.MONAI_LABEL_API_STR)

```

## ADD this line in Lib\site-packages\monailabel\interfaces\app.py at 106

```PYTHON
self.init_workflows()

```

## **License**

This project is licensed under the MIT License. Feel free to modify and distribute it as needed
