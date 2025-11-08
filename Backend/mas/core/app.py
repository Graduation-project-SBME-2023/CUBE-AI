"""
FastAPI wrapper around the MAS orchestrator.

POST /run
    {
        "hypothesis": "Free‑form clinical hypothesis",
        "tools_doc": "... optional description of available modules ..."
    }

The endpoint creates an ``Orchestrator`` instance, forwards the hypothesis to
``Orchestrator.process_hypothesis`` and returns the combined result:

{
    "planner_result": {...},
    "execution_state": {...},
    "system_state": "completed" | "failed"
}
"""

import asyncio
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import traceback
from fastapi import WebSocket, WebSocketDisconnect

# ----------------------------------------------------------------------
# Import the orchestrator (absolute import works because ``mas.core`` is a
# package inside the repository)
# ----------------------------------------------------------------------
from orchestrator import Orchestrator, SystemState

# ----------------------------------------------------------------------
# FastAPI app definition
# ----------------------------------------------------------------------
app = FastAPI(
    title="MAS Orchestrator API",
    description="Receive a clinical hypothesis, run the Planner → Executer pipeline, and return the result.",
    version="0.1.0",
)

# Allow calls from any origin (adjust for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------------------
# Request model – what the client must send
# ----------------------------------------------------------------------
class RunRequest(BaseModel):
    hypothesis: str = Field(..., description="Free form clinical hypothesis")



# ----------------------------------------------------------------------
# POST /run – core entry point
# ----------------------------------------------------------------------
@app.post("/run")
async def run_pipeline(request: RunRequest):
    """
    Run the full MAS pipeline for the supplied hypothesis.

    Returns the same dictionary that the CLI version prints:
    {
        "planner_result": {...},
        "execution_state": {...},
        "system_state": "completed" | "failed"
    }
    """
    # ------------------------------------------------------------------
    # 1️⃣  Create a fresh orchestrator instance
    # ------------------------------------------------------------------
    orchestrator = Orchestrator()

    # ------------------------------------------------------------------
    # 2️⃣  Forward the hypothesis (and optional tools doc) to the async
    #     ``process_hypothesis`` method.
    # ------------------------------------------------------------------
    try:
        result = await orchestrator.process_hypothesis(
            hypothesis_text=request.hypothesis,
        )
    except Exception as exc:
        # Log the error and return a proper HTTP 500 response
        raise HTTPException(status_code=500, detail=str(exc))

    # ------------------------------------------------------------------
    # 3️⃣  Return the result as JSON
    # ------------------------------------------------------------------
    return result


# ----------------------------------------------------------------------
# Optional: run the API with ``python app.py`` for quick local testing
# ----------------------------------------------------------------------

    
    
    # Add these imports near the top of your file

# --- WebSocket endpoint for streaming / real-time updates ---
@app.websocket("/ws")
async def websocket_run_pipeline(websocket: WebSocket):
    await websocket.accept()

    async def send_message(msg_type, payload):
        try:
            await websocket.send_text(json.dumps({
                "type": msg_type,
                "payload": payload
            }, default=str))
        except Exception:
            # client disconnected or closed
            return

    try:
        data = await websocket.receive_text()
        msg = json.loads(data)
        hypothesis = msg.get("hypothesis")
        tools_doc = msg.get("tools_doc", "")

        orchestrator = Orchestrator()

        # Run the orchestrator in background
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,  # default ThreadPoolExecutor
            lambda: asyncio.run(orchestrator.process_hypothesis(hypothesis))
        )

        await send_message("final", result)
    except WebSocketDisconnect:
        print("❌ Client disconnected")
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass


if __name__ == "__main__":
    import uvicorn

    # Resolve the config / log files relative to the repository root
    config_path = Path(__file__).parent / "config.json"
    log_path = Path(__file__).parent / "system.log"

    # The orchestrator itself will read those files when it is instantiated,
    # but we pass them explicitly here in case you want to customise them.
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,  # auto‑reload on code changes (development only)
    )
    