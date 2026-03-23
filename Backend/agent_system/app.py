import asyncio
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from teams.execution_team import BasicPipelineBuilder
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
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
import aiofiles
import json
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(root_dir)

from socket_store import manager
from agents.planning_agents.planner import ClinicalPlanner
from models.session import Session
from models.plans import PlannerState
from view_emitter import (
    emit_population_output,
    emit_segmentation_output,
    emit_features_output,
    emit_modeling_output,
    emit_report_output,
    emit_latex_output,
)
from client_config import (
    ClientConfig,
    get_client_config,
    set_client_config,
    create_model_client_from_config,
)

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
class CoopCoepMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response

app.add_middleware(CoopCoepMiddleware)
# Mount the workspace folder so files can be accessed via URL
# Adjust the path to point exactly to your workspace folder
workspace_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "workspace"))
app.mount("/static", StaticFiles(directory=workspace_dir), name="static")



# Single shared service — the only stateful object routes need to know about
session_service = SessionService(ws_manager=manager)

api_key = os.getenv("OLLAMA_API_KEY", "cf8ed2bae88d4d659602bc340b4b2bcf.mEvRr0YQ54A_OKfTT2Z92rge")


# ── Agent-to-view mapping for tool result emission ────────────────────────────

def _parse_tool_content(content: str) -> dict:
    """Attempt to parse tool result content as JSON."""
    if not content:
        return {}
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return {"raw": str(content)[:2000]}


async def _read_agent_output_file(workspace_folder: str, agent_id: str) -> dict:
    """Read a persisted agent output JSON from the workspace outputs dir."""
    output_path = os.path.join(workspace_folder, "outputs", f"{agent_id}_output.json")
    try:
        if os.path.exists(output_path):
            async with aiofiles.open(output_path, 'r') as f:
                return json.loads(await f.read())
    except Exception:
        pass
    return {}


async def _emit_tool_result_to_view(
    source: str, tool_name: str, content: str,
    session_id: str, workspace_folder: str, logger
):
    """
    Inspect a completed tool call and emit structured data to the matching
    frontend view via WebSocket.
    """
    parsed = _parse_tool_content(content)

    # ── Population Planner agent ──────────────────────────────────────────
    if "population" in source.lower() or "study_population" in source.lower():
        if tool_name == "write_agent_output":
            agent_data = await _read_agent_output_file(workspace_folder, "study_population_planner_agent")
            payload = agent_data if agent_data else parsed
            await emit_population_output(payload, session_id)
        elif tool_name in ("generate_targets", "get_patient", "get_volume"):
            await emit_population_output({
                "tool": tool_name,
                "status": parsed.get("status", "completed"),
                "data": parsed,
            }, session_id)

    # ── Segmentation agent ────────────────────────────────────────────────
    elif "segmentation" in source.lower():
        if tool_name == "write_agent_output":
            agent_data = await _read_agent_output_file(workspace_folder, "segmentation_agent")
            payload = agent_data if agent_data else parsed
            await emit_segmentation_output(payload, session_id)
        elif tool_name == "test_module":
            await emit_segmentation_output({
                "tool": tool_name,
                "status": parsed.get("status", "completed"),
                "data": parsed,
            }, session_id)

    # ── Feature Extraction agent ──────────────────────────────────────────
    elif "feature_extraction" in source.lower():
        if tool_name == "write_agent_output":
            agent_data = await _read_agent_output_file(workspace_folder, "feature_extraction_agent")
            payload = agent_data if agent_data else parsed
            await emit_features_output(payload, session_id)
        elif tool_name == "PyRadiomicsFeatureExtractionTool":
            await emit_features_output({
                "tool": "radiomic_extraction",
                "status": parsed.get("status", "completed"),
                "csv_paths": parsed.get("csv_paths", {}),
                "num_subjects": parsed.get("num_subjects"),
                "num_features": parsed.get("num_features", {}),
                "mask_labels": parsed.get("mask_labels", []),
                "processing_time_seconds": parsed.get("processing_time_seconds"),
            }, session_id)
        elif tool_name == "FeatureImportanceAnalysisTool":
            await emit_features_output({
                "tool": "feature_importance",
                "status": parsed.get("status", "completed"),
                "method": parsed.get("method"),
                "feature_count": parsed.get("feature_count"),
                "result_files": parsed.get("result_files", {}),
                "target_column": parsed.get("target_column"),
                "task_type": parsed.get("task_type"),
            }, session_id)

    # ── Modeling agent ────────────────────────────────────────────────────
    elif "modeling" in source.lower():
        if tool_name == "write_agent_output":
            agent_data = await _read_agent_output_file(workspace_folder, "modeling_agent")
            payload = agent_data if agent_data else parsed
            await emit_modeling_output(payload, session_id)
        elif tool_name == "classification_modelling":
            await emit_modeling_output({
                "tool": "classification",
                "status": parsed.get("status", "completed"),
                "experiment_name": parsed.get("experiment_name"),
                "model_summary": parsed.get("model_summary"),
                "final_model_path": parsed.get("final_model_path"),
                "created_files": parsed.get("created_files", []),
                "individual_model_paths": parsed.get("individual_model_paths", {}),
                "model_test_predictions": parsed.get("model_test_predictions", {}),
            }, session_id)

    # ── Result Reporter agent ─────────────────────────────────────────────
    elif "result_reporter" in source.lower():
        if tool_name == "write_agent_output":
            agent_data = await _read_agent_output_file(workspace_folder, "result_reporter_agent")
            payload = agent_data if agent_data else parsed
            await emit_report_output(payload, session_id)
        elif tool_name == "get_modeling_results":
            await emit_report_output({
                "tool": tool_name,
                "status": "completed",
                "data": parsed,
            }, session_id)

    # ── LaTeX Reporter agent ──────────────────────────────────────────────
    elif "latex" in source.lower() or "reporter" in source.lower():
        if tool_name == "write_agent_output":
            agent_data = await _read_agent_output_file(workspace_folder, "latex_reporter_agent")
            payload = agent_data if agent_data else parsed
            await emit_latex_output(payload, session_id)
        elif tool_name == "latex_to_pdf":
            await emit_latex_output({
                "tool": "latex_to_pdf",
                "status": parsed.get("status", "completed"),
                "data": parsed,
            }, session_id)


# ----------------------------------------------------------------------
# Planner callable — receives session + message, calls back into service
# ----------------------------------------------------------------------

async def run_planner(session: Session, message: str) -> None:
    base_agents_workspace = os.environ.get("AGENTS_WORKSPACE_DIR", current_dir)
    workspace_folder = os.path.join(
        base_agents_workspace, f"workspace_{session.session_id}"
    )
    os.makedirs(workspace_folder, exist_ok=True)
    planner = ClinicalPlanner(
        input_provider=lambda: manager.wait_for_user_message(),
        output_callback=lambda t, d, s: manager.send_ws_message(t, d, s),
        model_client=create_model_client_from_config(),
        workspace_dir=workspace_folder,
        session=session,
        session_service=session_service,
        api_key=api_key
    )
    await planner.run(start_query=message)
    logging.getLogger('HypothesisFlow').info(
        '[HypothesisFlow][Planner] Planner finished | session_id=%s',
        session.session_id,
    )


# ----------------------------------------------------------------------
# Executor callable — receives session, calls back into service
# ----------------------------------------------------------------------

async def run_pipeline(session: Session) -> None:
    base_agents_workspace = os.environ.get("AGENTS_WORKSPACE_DIR", current_dir)
    workspace_folder = os.path.join(base_agents_workspace, f"workspace_{session.session_id}")
    pipeline_logger = logging.getLogger("Execution Pipeline")
    logging.getLogger("autogen_core").setLevel(logging.CRITICAL)
    logging.getLogger("autogen_core.events").setLevel(logging.CRITICAL)

    # Load planner state: hypothesis + task messages (if available)
    state_file = os.path.join(workspace_folder, "state.json")
    task_messages = []
    hypothesis = None
    try:
        if os.path.exists(state_file):
            async with aiofiles.open(state_file, 'r') as f:
                content = await f.read()
                if content.strip():
                    planner_state = PlannerState.model_validate_json(content)
                    task_messages = planner_state.task_messages
                    hypothesis = planner_state.hypothesis.title
                    logger.info(
                        f"Loaded {len(task_messages)} task messages from planner state"
                    )
    except Exception as e:
        logger.warning(f"Could not load planner state: {e}. Using raw hypothesis.")

    if not hypothesis:
        hypothesis = json.load(
            open(state_file)
        ).get("hypothesis", {}).get("title")

    pipeline_logger.info(f"workspace_folder: {workspace_folder}, hypothesis: {hypothesis}")

    # Allow pause / cancel before heavy init
    await session.pause_event.wait()
    if session.cancel_event.is_set():
        raise asyncio.CancelledError()

    graph_builder = await BasicPipelineBuilder.create(
        workspace_folder=workspace_folder,
        task_messages=task_messages,
        model_client=create_model_client_from_config(),
    )

    print(graph_builder.get_participants())

    graph_flow = GraphFlow(
        participants=graph_builder.get_participants(),
        graph=graph_builder.build(),
    )

    # Build the pipeline prompt: use structured task messages if available,
    # otherwise fall back to the raw hypothesis string
    task_prompt = graph_builder.get_pipeline_prompt(fallback_hypothesis=hypothesis)
    pipeline_logger.info(f"Pipeline prompt length: {len(task_prompt)} chars")

    pipeline_logger.info("Starting execution...")

    try:
        async for event in graph_flow.run_stream(task=task_prompt):
            if session.cancel_event.is_set():
                raise asyncio.CancelledError()
            await session.pause_event.wait()

            if isinstance(event, TaskResult):
                pipeline_logger.info("━" * 60)
                pipeline_logger.info("✅ TASK COMPLETE")
                for msg in event.messages:
                    pipeline_logger.info(f"  [{msg.source}] {msg.content}")
                pipeline_logger.info("━" * 60)
                await manager.send_ws_message("run_finished", "run_finished", session_id=session.session_id)

            elif isinstance(event, ToolCallRequestEvent):
                pipeline_logger.info(f"🔧 [{event.source}] TOOL CALL REQUEST")
                await manager.send_ws_message("run_status", {"current_agent": event.source}, session_id=session.session_id)
                for call in event.content:
                    pipeline_logger.info(f"   Tool: {call.name}  Args: {call.arguments}")

            elif isinstance(event, ToolCallExecutionEvent):
                pipeline_logger.info(f"🔧 [{event.source}] TOOL CALL RESULT")
                await manager.send_ws_message("run_status", {"current_agent": event.source}, session_id=session.session_id)
                for result in event.content:
                    pipeline_logger.info(f"   Tool: {result.name}  Result: {result.content}")
                    await _emit_tool_result_to_view(
                        event.source, result.name, result.content,
                        session.session_id, workspace_folder, pipeline_logger
                    )

            elif isinstance(event, MemoryQueryEvent):
                pipeline_logger.info(f"🧠 [{event.source}] MEMORY QUERY: {event.content}")
                await manager.send_ws_message("run_status", {"current_agent": event.source}, session_id=session.session_id)

            elif isinstance(event, UserInputRequestedEvent):
                pipeline_logger.info(f"⏳ [{event.source}] WAITING FOR USER INPUT")

            elif isinstance(event, ThoughtEvent):
                pipeline_logger.info(f"💭 [{event.source}] THOUGHT: {event.content}")
                await manager.send_ws_message("run_status", {"current_agent": event.source}, session_id=session.session_id)

            elif isinstance(event, BaseChatMessage):
                logger.info("─" * 60)
                pipeline_logger.info(f"💬 [{event.source}] ({type(event).__name__}): {event.content}")
                await manager.send_ws_message("run_status", {"current_agent": event.source}, session_id=session.session_id)

            elif isinstance(event, BaseAgentEvent):
                pipeline_logger.info(f"⚙️  [{event.source}] ({type(event).__name__})")
                if hasattr(event, "content") and event.content:
                    pipeline_logger.info(f"   {event.content}")
                await manager.send_ws_message("run_status", {"current_agent": event.source}, session_id=session.session_id)
    except Exception as e:
        logger.error(e)


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
# Client configuration endpoints
# ----------------------------------------------------------------------

@app.get("/client-config")
async def get_config():
    """Return the current global LLM client configuration."""
    cfg = get_client_config()
    data = cfg.model_dump()
    # Mask the API key for security
    if data.get("api_key"):
        data["api_key"] = data["api_key"][:8] + "..." if len(data["api_key"]) > 8 else "***"
    return data


@app.post("/client-config")
async def update_config(config: ClientConfig):
    """Update the global LLM client configuration."""
    set_client_config(config)
    return {"status": "ok", "provider": config.provider, "model": config.model}


@app.get("/client-config/providers")
async def list_providers():
    """Return available provider options and their required/optional fields."""
    return {
        "providers": [
            {
                "id": "openai",
                "label": "OpenAI",
                "fields": ["model", "api_key"],
                "optional_fields": ["base_url", "temperature", "max_tokens", "top_p"],
                "description": "OpenAI GPT models or any OpenAI-compatible endpoint",
            },
            {
                "id": "azure_openai",
                "label": "Azure OpenAI",
                "fields": ["model", "api_key", "azure_endpoint", "api_version"],
                "optional_fields": ["azure_deployment", "temperature", "max_tokens", "top_p"],
                "description": "Azure-hosted OpenAI models",
            },
            {
                "id": "ollama",
                "label": "Ollama",
                "fields": ["model"],
                "optional_fields": ["base_url", "api_key", "temperature", "max_tokens", "top_p"],
                "description": "Local or remote Ollama instance (OpenAI-compatible)",
            },
        ]
    }


# ----------------------------------------------------------------------
# WebSocket /ws
# ----------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    logging.getLogger('HypothesisFlow').info('[HypothesisFlow][WS] Client connected')

    try:
        while True:
            data = await websocket.receive_text()
            await manager.push_to_queue(data)
            logging.getLogger('HypothesisFlow').info(
                '[HypothesisFlow][WS] Message received raw | size=%s',
                len(data or ''),
            )

            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                # Backward compatibility: plain text is treated as user input.
                await manager.push_to_queue(data)
                continue

            if not isinstance(message, dict):
                continue

            msg_type = message.get("type")
            payload = message.get("payload")
            session_id = message.get("session_id")

            # Some clients may nest session_id inside payload.
            if not session_id and isinstance(payload, dict):
                session_id = payload.get("session_id")

            if msg_type in {"user", "user_input", "chat_input"}:
                if isinstance(payload, dict):
                    text = payload.get("text") or payload.get("message") or ""
                else:
                    text = str(payload or "")

                if text.strip():
                    logging.getLogger('HypothesisFlow').info(
                        '[HypothesisFlow][WS] Queueing user text | session_id=%s | text_len=%s | preview=%s',
                        session_id,
                        len(text),
                        text[:120],
                    )
                    await manager.push_to_queue(text, session_id=session_id)

    except WebSocketDisconnect:
        logging.getLogger('HypothesisFlow').info('[HypothesisFlow][WS] Client disconnected')
        manager.disconnect(websocket)
        
@app.post("/ws-broadcast")
async def ws_broadcast(data: dict):
    await manager.send_ws_message(
        data["type"],
        data["payload"],
        session_id=data.get("session_id")
    )
    return {"status": "ok"}

# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8150, reload=True)
