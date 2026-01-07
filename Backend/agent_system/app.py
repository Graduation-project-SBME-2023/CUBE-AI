import asyncio
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Literal, Any
from datetime import datetime
from pydantic import BaseModel, Field
# from teams.execution_team import BasicPipelineBuilder
# from autogen_agentchat.teams import GraphFlow
import logging

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(root_dir)

from socket_store import manager, send_ws_message
from agents.planning_agents.planner import ClinicalPlanner
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


# ----------------------------------------------------------------------
# Request model
# ----------------------------------------------------------------------
class RunRequest(BaseModel):
    hypothesis: str = Field(..., description="Free form clinical hypothesis")

class PlanningRequest(BaseModel):
    message: str = Field(..., description="Initial message or clinical hypothesis from the user to start planning")

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


@app.post("/plan")
async def start_planning(request: PlanningRequest):
    """
    Initiates and runs the Clinical Planner synchronously.
    The HTTP request will wait until the entire planning process (including human interaction) is complete.
    Returns the final plan and hypothesis.
    
    The HTTP request to /plan will remain open (pending) for the entire duration of the planning session.
    If the session takes longer than your HTTP client's timeout setting (usually 30–60 seconds),
    the client might throw a timeout error, even though the process is still running on the server.
    """
    
    
    async def websocket_input_provider() -> str:
        return await manager.wait_for_user_message()

    async def websocket_output_callback(msg_type: str, data: Any):
        try:
            await manager.send_ws_message(msg_type, data)
        except AttributeError:
            pass
    api_key = os.getenv("OLLAMA_API_KEY", "cf8ed2bae88d4d659602bc340b4b2bcf.mEvRr0YQ54A_OKfTT2Z92rge")
    
    planner_instance = ClinicalPlanner(
        input_provider=websocket_input_provider,
        output_callback=websocket_output_callback,
        api_key=api_key
    )

    try:
        final_result = await planner_instance.run(start_query=request.message)
        return {"status": "finished", "result": final_result}
    except Exception as e:
        # Gracefully handle errors during the long-running process
        raise HTTPException(status_code=500, detail=f"Planning session failed: {str(e)}")


# ----------------------------------------------------------------------
# POST /run
# ----------------------------------------------------------------------
@app.post("/run")
async def run_pipeline(request: RunRequest):

    # graph_builder = await BasicPipelineBuilder.create()
    # graph_flow = GraphFlow(participants=graph_builder.get_participants(), graph=graph_builder.build())

    try:
        # stream = graph_flow.run_stream(task=request.hypothesis) # Not clean, still needs to connect to websocket?
        # async for message in stream:
        #     # print(message)
        #     pass
        print("Running pipeline with hypothesis:", request.hypothesis)

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
