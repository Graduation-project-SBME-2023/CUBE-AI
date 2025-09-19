import logging

from fastapi import APIRouter
from monailabel.interfaces.utils.app import app_instance

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/hello_world",
    tags=["Test"],
    responses={404: {"description": "Not found"}},
)


@router.get("/", summary="Hello World Endpoint", response_description="Returns a hello world message")
async def hello_world():
    """
    A simple endpoint that returns a hello world message.

    :return: A JSON response with a hello world message.
    """
    logger.info("Hello World endpoint called")
    instance = app_instance()
    logger.info(f"App instance: {instance}")
    return {"message": "Hello, World!"}
