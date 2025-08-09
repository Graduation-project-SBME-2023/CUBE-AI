import uvicorn
import os
import patches

from monailabel.app import app as monai_app
from monailabel.endpoints import datastore as monai_datastore
from endpoints.my_endpoints_datastore import router as my_datastore_router

from endpoints import hello_world, test_dicom
monai_app.include_router(hello_world.router)
monai_app.include_router(test_dicom.router)

# Include original MONAI Label datastore endpoints
monai_app.include_router(monai_datastore.router)

# Include  extended datastore endpoints with the same prefix
monai_app.include_router(my_datastore_router,    prefix="/datastore",  tags=["Datastore"] # OpenAPI tag for documentation grouping
)



if __name__ == "__main__":
    hot_reload = os.getenv("MONAI_LABEL_RELOAD", "false").lower() == "true"
    uvicorn.run("server:monai_server_app", host="0.0.0.0", port=8000, reload=hot_reload, reload_dirs=["/monailabel", "/apps/radiology"])
