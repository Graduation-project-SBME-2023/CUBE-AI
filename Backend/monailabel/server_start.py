import uvicorn
import os

from startup_logging import uvicorn_log_config

if __name__ == "__main__":

    hot_reload = os.getenv("MONAI_LABEL_RELOAD", "false").lower() == "true"
    uvicorn.run(
        "server:monai_server_app",
        host="0.0.0.0",
        port=8000,
        reload=hot_reload,
        reload_dirs=["/monailabel", "/apps/radiology"],
        log_config=uvicorn_log_config("MONAI Label"),
    )


