from monailabel.app import app as monai_server_app

from endpoints import (
    hello_world,
    create_model,
    workflow,
    norm_align,
    new_handle,
)

monai_server_app.include_router(workflow.router)
monai_server_app.include_router(create_model.router)
monai_server_app.include_router(new_handle.router)
monai_server_app.include_router(norm_align.router)
monai_server_app.include_router(hello_world.router)
