from monailabel.app import app as monai_server_app

from endpoints import (
    create_model,
    inference_jobs,
    my_endpoints_datastore,
    new_handle,
    norm_align,
    training_jobs,
    voxtell_cache_inventory,
)

for router in (
    create_model.router,
    new_handle.router,
    norm_align.router,
    my_endpoints_datastore.router,
    inference_jobs.router,
    training_jobs.router,
    voxtell_cache_inventory.router,
):
    monai_server_app.include_router(router)

import os
import threading

@monai_server_app.post("/restart")
def restart_monailabel():
    def kill_server():
        os._exit(1)
    threading.Timer(0.5, kill_server).start()
    return {"status": "restarting"}
