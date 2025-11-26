"""
FastAPI wrapper around the MAS orchestrator.
"""

import asyncio
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from orchestrator import Orchestrator, SystemState
from planner import Planner

# 🔥 NEW: socket store
from socket_store import set_socket, get_socket, send_ws_message

# ----------------------------------------------------------------------
# FastAPI app definition
# ----------------------------------------------------------------------
app = FastAPI(
    title="MAS Orchestrator API",
    description="Receive a clinical hypothesis, run the Planner → Executer pipeline, and return the result.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

planner = Planner()

# ----------------------------------------------------------------------
# Request model
# ----------------------------------------------------------------------
class RunRequest(BaseModel):
    hypothesis: str = Field(..., description="Free form clinical hypothesis")

# ----------------------------------------------------------------------
# POST /run
# ----------------------------------------------------------------------
@app.post("/run")
async def run_pipeline(request: RunRequest):

    orchestrator = Orchestrator()

    try:
        result = await orchestrator.process_hypothesis(
            hypothesis_text=request.hypothesis,
        )
        
        # 🔥 Example usage: push result to websocket if connected
        await send_ws_message("result", result)

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return result


# ----------------------------------------------------------------------
# WebSocket /ws – Streaming interface
# ----------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_run_pipeline(websocket: WebSocket):
    await websocket.accept()

    # store globally
    await set_socket(websocket)

    try:
        # First message from client (contains hypothesis)
        data = await websocket.receive_text()
        print("Received data:", data)
        msg = json.loads(data)
        data = msg.get("payload")

        # Let the planner run
    
        # If your planner wants to stream messages:
        #    it can now call send_ws_message("step", "...") internally
        result = await planner.run(data)
        # await send_ws_message("msg", "Starting pipeline...")
        # await send_ws_message("plan", {"steps": [{"id": "s1", "name": "Step 1", "description": "Description of step 1"}]})
        # # Send final response
        await send_ws_message("final", "Pipeline completed")

    except WebSocketDisconnect:
        print("Client disconnected")
    finally:
        # remove socket when closed
        set_socket(None)


# ----------------------------------------------------------------------
# Run manually (uvicorn)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8150,
        reload=True,
    )
