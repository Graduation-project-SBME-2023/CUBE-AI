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

# NEW: socket storemaset_socket, get_socket, send_ws_message
from socket_store import manager, send_ws_message
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
        
        # Example usage: push result to websocket if connected
        await send_ws_message("result", result)

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return result


# ----------------------------------------------------------------------
# WebSocket /ws – Streaming interface
# ----------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # 1. Register connection
    await manager.connect(websocket)
    
    # 2. Start your Planner Logic (in background)
    planner = Planner()
    asyncio.create_task(planner.run("Start"))


    # asyncio.create_task(planner.run("Start"))
    # Orchestrator.create_task(...)

    try:
        while True:
            # 3. LISTEN LOOP
            # This is the only place we call receive_text()
            data = await websocket.receive_text()
            
            # 4. Feed the Queue (This wakes up the Planner)
            await manager.push_to_queue(data)
            
    except WebSocketDisconnect:
        manager.disconnect(websocket)

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
