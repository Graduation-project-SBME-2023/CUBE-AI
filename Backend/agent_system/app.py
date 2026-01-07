import asyncio
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Literal, Any, Union
from datetime import datetime
from pydantic import BaseModel, Field
from teams.execution_team import BasicPipelineBuilder
from autogen_agentchat.teams import GraphFlow
from autogen_agentchat.messages import BaseAgentEvent, BaseChatMessage
from autogen_agentchat.base import TaskResult
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

ContentType = Literal[
    "markdown",
    "progress",
    "text",  # could add more content types
]

class MarkdownPayload(BaseModel):
    markdown: str

class ProgressPayload(BaseModel):
    current: int
    total: int
    label: str | None = None

class TextPayload(BaseModel):
    message: str

ContentPayload = Union[
    MarkdownPayload,
    ProgressPayload,
    TextPayload,
]

class ToolEvent(BaseModel):
    event_type: EventType
    source: str          # tool name or agent
    content_type: ContentType
    content: ContentPayload
    timestamp: datetime


@app.post("/plan")
async def start_planning(request: PlanningRequest):
    asyncio.create_task(run_planner(request.message))
    return {"task_id": "null for now", "status": "started"}

async def run_planner(message: str):
    """
    Initiates and runs the Clinical Planner synchronously.
    The HTTP request will wait until the entire planning process (including human interaction) is complete.
    Returns the final plan and hypothesis.
    
    The HTTP request to /plan will remain open (pending) for the entire duration of the planning session.
    If the session takes longer than your HTTP client's timeout setting (usually 30–60 seconds),
    the client might throw a timeout error, even though the process is still running on the server.
    """
    api_key = os.getenv("OLLAMA_API_KEY", "cf8ed2bae88d4d659602bc340b4b2bcf.mEvRr0YQ54A_OKfTT2Z92rge")
    planner = ClinicalPlanner(
        input_provider=lambda: manager.wait_for_user_message(),
        output_callback=lambda t, d: manager.send_ws_message(t, d),
        api_key=api_key
    )

    try:
        await planner.run(start_query=message)
    except Exception as e:
        manager.send_ws_message("error", str(e))

# ----------------------------------------------------------------------
# POST /run
# ----------------------------------------------------------------------
@app.post("/run")
async def run_pipeline(request: RunRequest):

    # graph_builder = await BasicPipelineBuilder.create()
    # graph_flow = GraphFlow(participants=graph_builder.get_participants(), graph=graph_builder.build())

    try:
        async for event in graph_flow.run_stream(task=request.hypothesis):
            if isinstance(event, TaskResult):
                await send_ws_message("run_finished", "run_finished")
            
            if isinstance(event, BaseChatMessage):
                logging.getLogger().info(f"agent {event.source} probably finished running??")
                await send_ws_message("run_status", {"current_agent": event.source})
            
            if isinstance(event, BaseAgentEvent):
                logging.getLogger().info(f"BaseAgentEvent: {event}")
                logging.getLogger().info(f"agent {event.source} is currently running.")
                await send_ws_message("run_status", {"current_agent": event.source})

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # return stream


@app.post("/events")
async def receive_tool_event(event: ToolEvent):
    logging.getLogger().info(f"event at /events: {event}")
    await send_ws_message(
        msg_type="tool_update",
        payload=event.model_dump()
        # payload=event
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
