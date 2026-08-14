import os

import uvicorn

from application_api.startup_logging import uvicorn_log_config


if __name__ == "__main__":
    uvicorn.run(
        "application_api.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("APP_API_PORT", os.environ.get("CONTROL_API_PORT", "8001"))),
        reload=False,
        workers=int(os.environ.get("APP_API_WORKERS", "2")),
        log_config=uvicorn_log_config("Application API"),
    )
