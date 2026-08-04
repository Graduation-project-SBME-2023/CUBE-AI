import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "control_server:control_app",
        host="0.0.0.0",
        port=int(os.environ.get("CONTROL_API_PORT", "8001")),
        reload=False,
        workers=1,
    )
