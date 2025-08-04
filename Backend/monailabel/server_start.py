import uvicorn
from monailabel.app import app as monai_app

from endpoints import (
    hello_world
)

monai_app.include_router(hello_world.router)


if __name__ == "__main__":
    uvicorn.run(monai_app, host="0.0.0.0", port=8000)
