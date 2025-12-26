import asyncio
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Literal, Any
from datetime import datetime
from pydantic import BaseModel, Field
from teams.execution_team import BasicPipelineBuilder
from autogen_agentchat.teams import GraphFlow
import logging

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(root_dir)

from mas.core.socket_store import manager, send_ws_message
from mas.core.planner import Planner
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

EventType = Literal[
    "tool_started",
    "tool_progress",
    "tool_log",
    "tool_finished",
    "run_started",
    "run_finished",
    "error"
]

class ToolEvent(BaseModel):
    event_type: EventType
    source: str          # tool name or agent
    payload: Any
    timestamp: datetime

# ----------------------------------------------------------------------
# POST /run
# ----------------------------------------------------------------------
@app.post("/run")
async def run_pipeline(request: RunRequest):

    graph_builder = await BasicPipelineBuilder.create()
    graph_flow = GraphFlow(participants=graph_builder.get_participants(), graph=graph_builder.build())

    try:
        stream = graph_flow.run_stream(task=request.hypothesis) # Not clean, still needs to connect to websocket?
        async for message in stream:
            # print(message)
            pass


    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # return stream


@app.post("/events")
async def receive_tool_event(event: ToolEvent):
    logging.getLogger().info(f"event at /events: {event}")
    await send_ws_message(
        msg_type=event.event_type,
        payload=event
    )

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
