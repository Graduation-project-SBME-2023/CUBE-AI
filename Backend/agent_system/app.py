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
import json

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(root_dir)

from socket_store import manager, send_ws_message
from agents.planning_agents.planner import ClinicalPlanner

from task_manager.session import Session
from task_manager.task_manager import TaskManager

from models.tool_logging import ToolEvent
from tool_logging import receive_tool_event


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

task_manager = TaskManager()


# ----------------------------------------------------------------------
# Request model
# ----------------------------------------------------------------------
class RunRequest(BaseModel):
    # hypothesis: str = Field(..., description="Free form clinical hypothesis")
    session_id: str = Field(..., description="Session ID")


class PlanningRequest(BaseModel):
    message: str = Field(..., description="Initial message or clinical hypothesis from the user to start planning")
    session_id: str = Field(..., description="Session ID")


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
    source: str  # tool name or agent
    content_type: ContentType
    content: ContentPayload
    timestamp: datetime


api_key = os.getenv("OLLAMA_API_KEY", "cf8ed2bae88d4d659602bc340b4b2bcf.mEvRr0YQ54A_OKfTT2Z92rge")
# planner = ClinicalPlanner(
#     input_provider=lambda: manager.wait_for_user_message(),
#     output_callback=lambda t, d: manager.send_ws_message(t, d),
#     api_key=api_key
# )


@app.post("/plan")
async def start_planning(request: PlanningRequest):
    # chat_session_id needs to be passed in the request.
    # planning and execution tasks need to be stored and controlled, and should communicate via ws
    print("Received planning request:", request.message)
    task_manager.start_planning(request.session_id, request.message, run_planner)
    # asyncio.create_task(run_planner(request.message))
    return {"task_id": request.session_id, "status": "started"}


async def run_planner(session: Session, message: str):
    """
    Initiates and runs the Clinical Planner synchronously.
    The HTTP request will wait until the entire planning process (including human interaction) is complete.
    Returns the final plan and hypothesis.
    
    The HTTP request to /plan will remain open (pending) for the entire duration of the planning session.
    If the session takes longer than your HTTP client's timeout setting (usually 30–60 seconds),
    the client might throw a timeout error, even though the process is still running on the server.
    """

    planner = ClinicalPlanner(
        input_provider=lambda: manager.wait_for_user_message(),
        output_callback=lambda t, d: manager.send_ws_message(t, d),
        api_key=api_key,
        session_id=session.session_id
    )

    # try:
    await planner.run(start_query=message)
    # except Exception as e:
    #     await manager.send_ws_message("error", str(e))


# ----------------------------------------------------------------------
# POST /run
# ----------------------------------------------------------------------
@app.post("/run")
async def start_execution(request: RunRequest):
    # chat_session_id needs to be passed in the request as well
    # task needs to be stored somehow and controlled,
    await task_manager.start_execution(request.session_id, run_pipeline)
    # asyncio.create_task(run_pipeline(request))
    return {"task_id": request.session_id, "status": "started"}


async def run_pipeline(session: Session):
    workspace_folder = os.path.join(
        current_dir, f"workspace_{session.session_id}"
    )

    hypothesis = json.load(
        open(os.path.join(workspace_folder, "state.json"))
    ).get("hypothesis", {}).get("title")

    logging.getLogger(
        "Execution Pipeline - runner task"
    ).info(f"workspace_folder: {workspace_folder}, hypothesis: {hypothesis}")

    # Allow pause / cancel before heavy init
    await session.pause_event.wait()
    if session.cancel_event.is_set():
        raise asyncio.CancelledError()

    graph_builder = await BasicPipelineBuilder.create(
        workspace_folder=workspace_folder
    )

    print(graph_builder.get_participants())

    graph_flow = GraphFlow(
        participants=graph_builder.get_participants(),
        graph=graph_builder.build(),
    )

    print(graph_flow)

    logging.getLogger(
        "Execution Pipeline - runner task"
    ).info("Starting execution...")

    try:
        async for event in graph_flow.run_stream(task=hypothesis):

            # ⛔ cooperative cancellation
            if session.cancel_event.is_set():
                raise asyncio.CancelledError()

            # ⏸ cooperative pause
            await session.pause_event.wait()

            if isinstance(event, TaskResult):
                await send_ws_message("run_finished", "run_finished")

            elif isinstance(event, BaseChatMessage):
                logging.getLogger(
                    "Execution Pipeline - runner task"
                ).info(f"agent {event.source} finished?")
                await send_ws_message(
                    "run_status", {"current_agent": event.source}
                )

            elif isinstance(event, BaseAgentEvent):
                logging.getLogger(
                    "Execution Pipeline - runner task"
                ).info(f"agent {event.source} is running")
                await send_ws_message(
                    "run_status", {"current_agent": event.source}
                )

    except asyncio.CancelledError:
        logging.getLogger(
            "Execution Pipeline - runner task"
        ).info("Execution cancelled")
        raise

    except Exception as exc:
        logging.exception("Execution failed")
        raise HTTPException(status_code=500, detail=str(exc))

    # return stream


@app.post("/events")
async def receive_event(event: ToolEvent):
    logging.getLogger().info(f"event at /events: {event}")
    await receive_tool_event(event)


# ----------------------------------------------------------------------
# WebSocket /ws – Streaming interface
# ----------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # 1. Register connection
    await manager.connect(websocket)
    # 2. Start your Planner Logic (in background)
    # asyncio.create_task(planner.run("Start"))

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
