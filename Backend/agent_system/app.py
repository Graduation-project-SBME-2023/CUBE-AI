import asyncio
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from teams.execution_team import BasicPipelineBuilder
from autogen_agentchat.teams import GraphFlow
from autogen_agentchat.messages import (
    ToolCallRequestEvent,
    ToolCallExecutionEvent,
    MemoryQueryEvent,
    UserInputRequestedEvent,
    ThoughtEvent,
    BaseChatMessage,
    BaseAgentEvent,
)
from autogen_agentchat.base import TaskResult
import logging
import json
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(root_dir)

from socket_store import manager
from agents.planning_agents.planner import ClinicalPlanner
from models.session import Session
from models.tool_logging import ToolEvent
from models.requests import PlanningRequest, SessionControlRequest
from tool_logging import receive_tool_event
from services.session_service import SessionService

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# App setup
# ----------------------------------------------------------------------

app = FastAPI(
    title="MAS Orchestrator API",
    description="Receive a clinical hypothesis, run the Planner → Executor pipeline.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Single shared service — the only stateful object routes need to know about
session_service = SessionService(ws_manager=manager)

api_key = os.getenv("OLLAMA_API_KEY", "cf8ed2bae88d4d659602bc340b4b2bcf.mEvRr0YQ54A_OKfTT2Z92rge")


# ----------------------------------------------------------------------
# Planner callable — receives session + message, calls back into service
# ----------------------------------------------------------------------

async def run_planner(session: Session, message: str) -> None:
    planner = ClinicalPlanner(
        input_provider=lambda: manager.wait_for_user_message(),
        output_callback=lambda t, d: manager.send_ws_message(t, d),
        api_key=api_key,
        session_id=session.session_id,
    )
    await planner.run(start_query=message)


# ----------------------------------------------------------------------
# Executor callable — receives session, calls back into service
# ----------------------------------------------------------------------

async def run_pipeline(session: Session) -> None:
    workspace_folder = os.path.join(current_dir, f"workspace_{session.session_id}")
    pipeline_logger = logging.getLogger("Execution Pipeline")
    logging.getLogger("autogen_core").setLevel(logging.CRITICAL)
    logging.getLogger("autogen_core.events").setLevel(logging.CRITICAL)

    hypothesis = json.load(
        open(os.path.join(workspace_folder, "state.json"))
    ).get("hypothesis", {}).get("title")

    pipeline_logger.info(f"workspace_folder: {workspace_folder}, hypothesis: {hypothesis}")

    # Allow pause / cancel before heavy init
    await session.pause_event.wait()
    if session.cancel_event.is_set():
        raise asyncio.CancelledError()

    graph_builder = await BasicPipelineBuilder.create(workspace_folder=workspace_folder)
    graph_flow = GraphFlow(
        participants=graph_builder.get_participants(),
        graph=graph_builder.build(),
    )

    pipeline_logger.info("Starting execution...")

    async for event in graph_flow.run_stream(task=hypothesis):
        if session.cancel_event.is_set():
            raise asyncio.CancelledError()

        await session.pause_event.wait()

        if session.cancel_event.is_set():
            raise asyncio.CancelledError()

        if isinstance(event, TaskResult):
            pipeline_logger.info("━" * 60)
            pipeline_logger.info("✅ TASK COMPLETE")
            for msg in event.messages:
                pipeline_logger.info(f"  [{msg.source}] {msg.content}")
            pipeline_logger.info("━" * 60)
            await manager.send_ws_message("run_finished", "run_finished")

        elif isinstance(event, ToolCallRequestEvent):
            pipeline_logger.info(f"🔧 [{event.source}] TOOL CALL REQUEST")
            await manager.send_ws_message("run_status", {"current_agent": event.source})
            for call in event.content:
                pipeline_logger.info(f"   Tool: {call.name}  Args: {call.arguments}")

        elif isinstance(event, ToolCallExecutionEvent):
            pipeline_logger.info(f"🔧 [{event.source}] TOOL CALL RESULT")
            await manager.send_ws_message("run_status", {"current_agent": event.source})
            for result in event.content:
                pipeline_logger.info(f"   Tool: {result.name}  Result: {result.content}")

        elif isinstance(event, MemoryQueryEvent):
            pipeline_logger.info(f"🧠 [{event.source}] MEMORY QUERY: {event.content}")
            await manager.send_ws_message("run_status", {"current_agent": event.source})

        elif isinstance(event, UserInputRequestedEvent):
            pipeline_logger.info(f"⏳ [{event.source}] WAITING FOR USER INPUT")

        elif isinstance(event, ThoughtEvent):
            pipeline_logger.info(f"💭 [{event.source}] THOUGHT: {event.content}")
            await manager.send_ws_message("run_status", {"current_agent": event.source})

        elif isinstance(event, BaseChatMessage):
            pipeline_logger.info(f"💬 [{event.source}] ({type(event).__name__}): {event.content}")
            await manager.send_ws_message("run_status", {"current_agent": event.source})

        elif isinstance(event, BaseAgentEvent):
            pipeline_logger.info(f"⚙️  [{event.source}] ({type(event).__name__})")
            if hasattr(event, "content") and event.content:
                pipeline_logger.info(f"   {event.content}")
            await manager.send_ws_message("run_status", {"current_agent": event.source})


# ----------------------------------------------------------------------
# Routes — thin, all logic delegated to session_service
# ----------------------------------------------------------------------

@app.post("/plan")
async def start_planning(request: PlanningRequest):
    session_service.start_planning(request.session_id, request.message, run_planner)
    return {"session_id": request.session_id, "status": "planning_started"}


@app.post("/run")
async def start_execution(request: SessionControlRequest):
    await session_service.start_execution(request.session_id, run_pipeline)
    return {"session_id": request.session_id, "status": "execution_started"}


@app.post("/pause")
async def pause_execution(request: SessionControlRequest):
    try:
        session_service.pause(request.session_id)
        return {"session_id": request.session_id, "status": "paused"}
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@app.post("/resume")
async def resume_execution(request: SessionControlRequest):
    try:
        session_service.resume(request.session_id)
        return {"session_id": request.session_id, "status": "running"}
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@app.post("/stop")
async def stop_execution(request: SessionControlRequest):
    try:
        session_service.stop(request.session_id)
        return {"session_id": request.session_id, "status": "stopped"}
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@app.post("/restart")
async def restart_execution(request: SessionControlRequest):
    try:
        await session_service.restart_execution(request.session_id, run_pipeline)
        return {"session_id": request.session_id, "status": "restarted"}
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@app.get("/status/{session_id}")
async def get_status(session_id: str):
    session = session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session_id,
        "state": session.state.value,
        "last_error": session.last_error,
    }


@app.post("/events")
async def receive_event(event: ToolEvent):
    logger.info(f"Received tool event: {event}")
    await receive_tool_event(event)


# ----------------------------------------------------------------------
# WebSocket /ws
# ----------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await manager.push_to_queue(data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8150, reload=True)
