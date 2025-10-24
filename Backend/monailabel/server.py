from monailabel.app import app as monai_server_app

from endpoints import (
    hello_world,
    create_model,
    workflow,
    norm_align,
    new_handle,
    my_endpoints_datastore,
    patient,
    study,
    volume,
    model,
    radiomics,
)

monai_server_app.include_router(workflow.router)
monai_server_app.include_router(create_model.router)
monai_server_app.include_router(new_handle.router)
monai_server_app.include_router(norm_align.router)
monai_server_app.include_router(hello_world.router)
monai_server_app.include_router(my_endpoints_datastore.router)
monai_server_app.include_router(patient.router)
monai_server_app.include_router(study.router)
monai_server_app.include_router(volume.router)
monai_server_app.include_router(model.router)
monai_server_app.include_router(radiomics.router)
