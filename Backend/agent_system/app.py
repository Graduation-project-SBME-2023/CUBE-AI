import asyncio

import re
import tempfile
import subprocess
import shutil
from typing import Dict

import httpx
# pyrefly: ignore [missing-import]
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query, File, UploadFile, Form
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi import APIRouter, UploadFile, File
from groq import BaseModel
import os
from teams.execution_team import BasicPipelineBuilder
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
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

from socket_store import manager as ws_manager, send_ws_message
from agents.planning_agents.planner import ClinicalPlanner
from models.session import Session
from models.plans import PlannerState
from models.logs import WS_Log
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
from services.pipeline_monitor import PipelineMonitor
from clients.backend_client import BackendClient

logger = logging.getLogger(__name__)

logging.getLogger("watchfiles").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
# ----------------------------------------------------------------------
# App setup
# ----------------------------------------------------------------------

app = FastAPI(
    title="MAS Orchestrator API",
    description="Receive a clinical hypothesis, run the Planner → Executor pipeline.",
    version="0.1.0",
)
_cors_origins = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:8150"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins],
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

agents_workspace_dir = os.environ.get("AGENTS_WORKSPACE_DIR", "/app/agents_workspace")
app.mount(
    "/files",
    StaticFiles(directory=agents_workspace_dir),
    name="files"
)

@app.get("/static/{file_path:path}")
def serve_static_pdf(file_path: str):
    # Resolve the requested path
    full_path = os.path.normpath(file_path)
    if not os.path.isabs(full_path):
        full_path = os.path.abspath(full_path)

    # Security: ensure the resolved path is strictly inside agents_workspace_dir.
    # This prevents path traversal attacks (e.g., /static/../../../etc/passwd).
    allowed_root = os.path.realpath(agents_workspace_dir)
    resolved = os.path.realpath(full_path)
    if not resolved.startswith(allowed_root + os.sep) and resolved != allowed_root:
        raise HTTPException(
            status_code=403,
            detail="Access denied: path is outside the allowed workspace directory."
        )

    if not os.path.exists(resolved):
        raise HTTPException(status_code=404, detail=f"File not found: {resolved}")

    if resolved.endswith(".pdf"):
        return FileResponse(
            path=resolved,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"inline; filename={os.path.basename(resolved)}"
            },
            content_disposition_type="inline",
        )

    # detect GIF explicitly
    if resolved.endswith(".gif"):
        return FileResponse(path=resolved, media_type="image/gif")

    return FileResponse(path=resolved)


# Single shared service — the only stateful object routes need to know about
session_service = SessionService(ws_manager=ws_manager)

api_key = os.getenv("OLLAMA_API_KEY", "")


# ----------------------------------------------------------------------
# Planner callable — receives session + message, calls back into service
# ----------------------------------------------------------------------


async def run_planner(session: Session, message: str, do_lit_review: bool = True) -> None:
    base_agents_workspace = os.environ.get("AGENTS_WORKSPACE_DIR", current_dir)
    workspace_folder = os.path.join(
        base_agents_workspace, f"workspace_{session.session_id}"
    )
    os.makedirs(workspace_folder, exist_ok=True)
    planner = ClinicalPlanner(
        input_provider=lambda: ws_manager.wait_for_user_message(session.session_id),
        output_callback=lambda t, d, s: ws_manager.send_ws_message(t, d, s),
        model_client=create_model_client_from_config(),
        workspace_dir=workspace_folder,
        session=session,
        session_service=session_service,
        api_key=api_key,
    )
    await planner.run(start_query=message, do_lit_review=do_lit_review)
    logging.getLogger("HypothesisFlow").info(
        "[HypothesisFlow][Planner] Planner finished | session_id=%s",
        session.session_id,
    )


# ----------------------------------------------------------------------
# Executor callable — receives session, calls back into service
# ----------------------------------------------------------------------


async def run_pipeline(session: Session) -> None:
    base_agents_workspace = os.environ.get("AGENTS_WORKSPACE_DIR", current_dir)
    workspace_folder = os.path.join(
        base_agents_workspace, f"workspace_{session.session_id}"
    )
    pipeline_logger = logging.getLogger("Execution Pipeline")
    logging.getLogger("autogen_core").setLevel(logging.CRITICAL)
    logging.getLogger("autogen_core.events").setLevel(logging.CRITICAL)

    # Load planner state: hypothesis + task messages (if available)
    state_file = os.path.join(workspace_folder, "state.json")
    task_messages = []
    hypothesis = None
    try:
        if os.path.exists(state_file):
            async with aiofiles.open(state_file, "r") as f:
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
        hypothesis = json.load(open(state_file)).get("hypothesis", {}).get("title")

    pipeline_logger.info(
        f"workspace_folder: {workspace_folder}, hypothesis: {hypothesis}"
    )

    # Allow pause / cancel before heavy init
    await session.pause_event.wait()
    if session.cancel_event.is_set():
        raise asyncio.CancelledError()

    graph_builder = await BasicPipelineBuilder.create(
        workspace_folder=workspace_folder,
        task_messages=task_messages,
        model_client=create_model_client_from_config(),
        session_id=session.session_id,
    )

    graph_flow = GraphFlow(
        participants=graph_builder.get_participants(),
        graph=graph_builder.build(),
    )


    # Build the pipeline prompt: use structured task messages if available,
    # otherwise fall back to the raw hypothesis string
    # task_prompt = graph_builder.get_pipeline_prompt(fallback_hypothesis=hypothesis)
    task_prompt = hypothesis
    pipeline_logger.info(f"Pipeline prompt length: {len(task_prompt)} chars")

    pipeline_logger.info("Starting execution...")

    session.execution_monitor = PipelineMonitor(session_service, session)

    await session.execution_monitor._broadcast()

    try:
        async for event in graph_flow.run_stream(task=task_prompt):
            if session.cancel_event.is_set():
                raise asyncio.CancelledError()
            await session.pause_event.wait()

            match event:
                case TaskResult(messages=msgs):
                    pipeline_logger.info("━" * 60)
                    pipeline_logger.info("✅ TASK COMPLETE")
                    for msg in msgs:
                        pipeline_logger.info(f"  [{msg.source}] {msg.content}")
                    pipeline_logger.info("━" * 60)
                    await session.execution_monitor.on_pipeline_complete()
                    await ws_manager.send_ws_message(
                        "run_finished", "run_finished", session_id=session.session_id
                    )

                case ToolCallRequestEvent(source=src, content=content):
                    pipeline_logger.info(f"🔧 [{src}] TOOL CALL REQUEST")
                    await session.execution_monitor.on_agent_active(src)
                    for call in content:
                        pipeline_logger.info(
                            f"     Tool: {call.name}  Args: {call.arguments}"
                        )

                case ToolCallExecutionEvent(source=src, content=content):
                    pipeline_logger.info(f"🔧 [{src}] TOOL CALL RESULT")
                    for result in content:
                        await session.execution_monitor.on_tool_result(
                            src, result.name, result.content, workspace_folder
                        )
                        pipeline_logger.info(
                            f"     Tool: {result.name}  Result: {result.content}"
                        )

                case MemoryQueryEvent(source=src, content=content):
                    pipeline_logger.info(f"🧠 [{src}] MEMORY QUERY: {content}")
                    await session.execution_monitor.on_agent_active(src)

                case UserInputRequestedEvent(source=src):
                    pipeline_logger.info(f"⏳ [{src}] WAITING FOR USER INPUT")

                case ThoughtEvent(source=src, content=content):
                    pipeline_logger.info(f"💭 [{src}] THOUGHT: {content}")
                    await session.execution_monitor.on_agent_active(src)

                case BaseChatMessage(source=src):
                    logger.info("━" * 60)
                    pipeline_logger.info(
                        f"💬 [{src}] ({type(event).__name__}): {event.content}"
                    )
                    await session.execution_monitor.on_agent_active(src)

                case BaseAgentEvent(source=src):
                    pipeline_logger.info(f"⚙️  [{src}] ({type(event).__name__})")
                    if hasattr(event, "content") and event.content:
                        pipeline_logger.info(f"   {event.content}")
                    await session.execution_monitor.on_agent_active(src)

    except asyncio.CancelledError:
        await session.execution_monitor.on_pipeline_error(
            Exception("Pipeline Cancelled")
        )
        raise
    except Exception as e:
        logger.error(f"here, {e} occured, check in this method")
        logger.error(e)
        await session.execution_monitor.on_pipeline_error(e)


# ----------------------------------------------------------------------
# Routes — thin, all logic delegated to session_service
# ----------------------------------------------------------------------


@app.post("/plan")
async def start_planning(request: PlanningRequest):
    from functools import partial
    bound_planner = partial(run_planner, do_lit_review=request.do_lit_review)
    session_service.start_planning(request.session_id, request.message, bound_planner)
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


class latex_output(BaseModel):
    event_type: str
    content: dict
    session_id: str


@app.post("/events")
async def receive_event(event: ToolEvent | latex_output):
    logger.info(f"Received tool event: {event}")
    if isinstance(event, latex_output):
        await send_ws_message(
            msg_type="latex_output",
            payload=event.content.get("frontend_data"),
            session_id=event.session_id,
        )
        return

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
        data["api_key"] = (
            data["api_key"][:8] + "..." if len(data["api_key"]) > 8 else "***"
        )
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
                "optional_fields": [
                    "azure_deployment",
                    "temperature",
                    "max_tokens",
                    "top_p",
                ],
                "description": "Azure-hosted OpenAI models",
            },
            {
                "id": "ollama",
                "label": "Ollama",
                "fields": ["model"],
                "optional_fields": [
                    "base_url",
                    "api_key",
                    "temperature",
                    "max_tokens",
                    "top_p",
                ],
                "description": "Local or remote Ollama instance (OpenAI-compatible)",
            },
        ]
    }


# ----------------------------------------------------------------------
# State Recovery
# ----------------------------------------------------------------------

@app.get("/session/{session_id}/replay-logs")
async def replay_logs(session_id: str):
    logs: list[WS_Log] = await BackendClient.get_logs(session_id)
    for log in logs:
        if log.msg_type in ('msg'): continue
        await ws_manager.send_ws_message(log.msg_type, log.payload, log.session_id, replaying=True)



@app.get("/session/{session_id}/config")
async def get_session_config(session_id: str):
    """Get the current configuration for a given session."""
    base_agents_workspace = os.environ.get("AGENTS_WORKSPACE_DIR", os.path.abspath(os.path.join(current_dir, "..", "agents_workspace")))
    workspace_folder = os.path.join(base_agents_workspace, f"workspace_{session_id}")
    config_path = os.path.join(workspace_folder, "config.json")
    
    if not os.path.exists(config_path):
        return JSONResponse(content={"labelling_mode": "automatic"}, status_code=200)
        
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
        return JSONResponse(content=config_data, status_code=200)
    except Exception as e:
        logger.error(f"Error reading config: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/session/{session_id}/plan-state")
async def get_plan_state(session_id: str):
    """
    Get plan state, hypothesis, and task messages for a session.
    Used when recovering a session from hypothesis history.
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    base_agents_workspace = os.environ.get("AGENTS_WORKSPACE_DIR", current_dir)
    workspace_folder = os.path.join(base_agents_workspace, f"workspace_{session_id}")

    if not os.path.exists(workspace_folder):
        raise HTTPException(status_code=404, detail="Workspace not found")

    state_file = os.path.join(workspace_folder, "state.json")
    result = {
        "session_id": session_id,
        "workspace_dir": workspace_folder,
        "hypothesis": None,
        "task_messages": [],
    }

    if os.path.exists(state_file):
        try:
            async with aiofiles.open(state_file, "r") as f:
                content = await f.read()
                if content.strip():
                    state_data = json.loads(content)
                    result["hypothesis"] = state_data.get("hypothesis")

                    task_messages = state_data.get("task_messages", [])
                    task_messages_json = state_data.get("task_messages_json", [])

                    if task_messages_json:
                        try:
                            result["task_messages"] = json.loads(task_messages_json)
                        except:
                            result["task_messages"] = task_messages
        except Exception as e:
            logger.warning(f"Could not load plan state: {e}")

    return result


@app.get("/session/{session_id}/execution-state")
async def get_execution_state(session_id: str):
    state = await session_service.recover_execution_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="No execution state found")
    return state


@app.get("/session/{session_id}/chat-messages")
async def get_session_chat_messages(session_id: str):
    """
    Fetch chat messages for a session from the MONAI server.
    Used when recovering a session from hypothesis history.
    """
    async with httpx.AsyncClient() as client:
        monai_url = os.environ.get("MONAI_URL", "http://localhost:8000")
        try:
            response = await client.get(
                f"{monai_url}/chat_messages/{session_id}", timeout=10.0
            )
            if response.status_code == 200:
                return response.json()
            else:
                return []
        except Exception as e:
            logger.warning(f"Could not fetch chat messages: {e}")
            return []


# ----------------------------------------------------------------------
# Hypothesis History
# ----------------------------------------------------------------------


@app.get("/hypothesis-history")
async def get_hypothesis_history(limit: int = 20):
    """
    Fetch hypothesis history from the backend API.
    Returns list of recent hypotheses with their chat session info.
    """
    history = await session_service.get_hypothesis_history(limit)

    return history


@app.post("/reports/compile")
async def compile_report(data: dict, request: Request):
    """
    Compile LaTeX text into a PDF inside the session workspace and return a public URL.

    Expected body:
      - session_id (str)
      - latex_text (str)
      - output_filename (str, optional)
    """
    session_id = str(data.get("session_id") or "").strip()
    latex_text = data.get("latex_text")
    output_filename = str(data.get("output_filename") or "final_report.pdf").strip()

    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    if not isinstance(latex_text, str) or not latex_text.strip():
        raise HTTPException(status_code=400, detail="latex_text is required")

    # Ensure filename cannot traverse directories and always ends in .pdf
    safe_name = os.path.basename(output_filename)
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", safe_name)
    if not safe_name.lower().endswith(".pdf"):
        safe_name = f"{safe_name}.pdf"

    workspace_name = f"workspace_{session_id}"
    workspace_folder = os.path.join(agents_workspace_dir, workspace_name)
    reports_folder = os.path.join(workspace_folder, "reports")
    os.makedirs(reports_folder, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir:
        tex_path = os.path.join(temp_dir, "document.tex")
        pdf_temp_path = os.path.join(temp_dir, "document.pdf")

        with open(tex_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(latex_text)

        compile_logs = []
        for _ in range(2):
            result = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "document.tex"],
                cwd=temp_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30.0,
            )
            stdout = result.stdout.decode("utf-8", errors="ignore")
            stderr = result.stderr.decode("utf-8", errors="ignore")
            compile_logs.append(stdout)
            if stderr:
                compile_logs.append(stderr)
            if result.returncode != 0:
                break

        if not os.path.exists(pdf_temp_path):
            merged_log = "\n".join(compile_logs)[-8000:]
            return {
                "status": "error",
                "message": "LaTeX compilation failed",
                "compile_log": merged_log,
            }

        final_path = os.path.join(reports_folder, safe_name)
        with open(pdf_temp_path, "rb") as src, open(final_path, "wb") as dst:
            dst.write(src.read())

    # Prefer explicit AGENTS_BASE_URL env var when provided (no hardcoded hosts)
    agents_base = os.environ.get("AGENTS_BASE_URL")
    if agents_base:
        pdf_url = agents_base.rstrip("/") + f"/files/{workspace_name}/reports/{safe_name}"
    else:
        base_url = str(request.base_url).rstrip("/")
        pdf_url = f"{base_url}/files/{workspace_name}/reports/{safe_name}"

    return {
        "status": "success",
        "output_filename": safe_name,
        "pdf_url": pdf_url,
        "pdf_path": final_path,
    }


# ----------------------------------------------------------------------
# WebSocket /ws
# ----------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    logging.getLogger("HypothesisFlow").info("[HypothesisFlow][WS] Client connected")

    try:
        while True:
            data = await websocket.receive_text()
            await ws_manager.push_to_queue(data)
            logging.getLogger("HypothesisFlow").info(
                "[HypothesisFlow][WS] Message received raw | size=%s",
                len(data or ""),
            )

            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                # Backward compatibility: plain text is treated as user input.
                await ws_manager.push_to_queue(data)
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
                    logging.getLogger("HypothesisFlow").info(
                        "[HypothesisFlow][WS] Queueing user text | session_id=%s | text_len=%s | preview=%s",
                        session_id,
                        len(text),
                        text[:120],
                    )
                    await ws_manager.push_to_queue(text, session_id=session_id)

    except WebSocketDisconnect:
        logging.getLogger("HypothesisFlow").info(
            "[HypothesisFlow][WS] Client disconnected"
        )
        ws_manager.disconnect(websocket)


@app.post("/ws-broadcast")
async def ws_broadcast(data: dict):
    await ws_manager.send_ws_message(
        data["type"], data["payload"], session_id=data.get("session_id")
    )
    return {"status": "ok"}
    
_review_futures: Dict[str, asyncio.Future] = {}

# ----------------------------------------------------------------------
# Population targets saving & mode endpoint
# ----------------------------------------------------------------------

@app.post("/populations/set-labelling-mode")
async def set_labelling_mode(
    session_id: str = Query(..., description="The ID of the current session"),
    mode: str = Query(..., description="Labelling mode: 'manual' or 'automatic'")
):
    if mode not in ["manual", "automatic"]:
        raise HTTPException(status_code=400, detail="mode must be 'manual' or 'automatic'")

    current_dir = os.path.dirname(os.path.abspath(__file__))
    base_agents_workspace = os.environ.get("AGENTS_WORKSPACE_DIR", current_dir)
    workspace_folder = os.path.join(base_agents_workspace, f"workspace_{session_id}")
    config_path = os.path.join(workspace_folder, "config.json")
    
    config_data = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except Exception:
            pass

    config_data["labelling_mode"] = mode

    os.makedirs(workspace_folder, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4)
        
    return {"status": "success", "mode": mode}


@app.post("/populations/upload-targets")
async def upload_targets(
    session_id: str = Query(..., description="The ID of the current session"),
    file: UploadFile = File(...)
):
    # 1. Get the base workspace directory, matching your run_planner logic
    current_dir = os.path.dirname(os.path.abspath(__file__))
    base_agents_workspace = os.environ.get("AGENTS_WORKSPACE_DIR", current_dir)
    
    # 2. Construct the exact path based on the session_id
    workspace_folder = os.path.join(base_agents_workspace, f"workspace_{session_id}")
    output_dir = os.path.join(workspace_folder, 'populations', 'targets')
    # 3. Create all necessary folders if they don't exist
    try:
        os.makedirs(output_dir, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create directories: {str(e)}")
    
    # 4. Define the final file path
    csv_path = os.path.join(output_dir, "targets.csv")

    # 5. Debug output to verify the absolute path
    print(f"========== DEBUG: SAVING TARGETS CSV TO ==========")
    print(os.path.abspath(csv_path))

    # 6. Save the uploaded file securely
    try:
        with open(csv_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write file: {str(e)}")

    return {
        "message": "Targets saved successfully", 
        "session_id": session_id,
        "path": os.path.abspath(csv_path)
    }
@app.post("/segmentation-review")
async def segmentation_review(data: dict):
    session_id = data.get("session_id", "default")
    review_payload = {
        "accepted": data.get("accepted", []),
        "rejected": data.get("rejected", []),
        "modified": data.get("modified", []),
    }
    logger.info(f"[Review] Received triage for session={session_id}: {review_payload}")
    
    # resolve the waiting module if it's already polling
    future = _review_futures.pop(session_id, None)
    if future and not future.done():
        future.set_result(review_payload)
        logger.info(f"[Review] Resolved waiting future for session={session_id}")
    else:
        # store it in case the module polls later
        _review_futures[f"ready:{session_id}"] = review_payload  # plain dict, not future
        logger.info(f"[Review] No active future found for session={session_id}. Stored as ready:{session_id} for later pickup.")
    
    return {"status": "ok"}


@app.post("/segmentation-review/upload-mask")
async def upload_modified_mask(
    session_id: str = Form(...),
    series_instance_uid: str = Form(...),
    file: UploadFile = File(...)
):
    current_dir = os.path.dirname(os.path.abspath(__file__))
    base_agents_workspace = os.environ.get("AGENTS_WORKSPACE_DIR", current_dir)
    workspace_folder = os.path.join(base_agents_workspace, f"workspace_{session_id}")
    output_dir = os.path.join(workspace_folder, 'temp_segmentations')
    
    try:
        os.makedirs(output_dir, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create directories: {str(e)}")
    
    file_path = os.path.join(output_dir, f"seg_{series_instance_uid}_modified.nii.gz")
    # The frontend mask matches the full DICOM volume space.
    # The workspace stores volumes in 'volumes/' and masks in 'masks/'.
    base_volume_path = os.path.join(workspace_folder, "volumes", f"{series_instance_uid}.nii.gz")
    original_mask_path = os.path.join(workspace_folder, "masks", f"{series_instance_uid}.nii.gz")
    
    try:
        import nibabel as nib
        import numpy as np
        import json
        
        # Read the raw byte array uploaded by the frontend
        raw_bytes = await file.read()
        actual_bytes = len(raw_bytes)
        
        # Try to load the base volume NIfTI for shape and affine
        ref_nii = None
        ref_shape = None
        
        if os.path.exists(base_volume_path):
            ref_nii = nib.load(base_volume_path)
            ref_shape = ref_nii.shape[:3]
            logger.info(f"Using base volume for reference: shape={ref_shape}")
        elif os.path.exists(original_mask_path):
            ref_nii = nib.load(original_mask_path)
            ref_shape = ref_nii.shape[:3]
            logger.info(f"Using original mask for reference: shape={ref_shape}")
        
        # If neither file exists, try to infer shape from the frontend-provided dimensions
        if ref_shape is None:
            raise FileNotFoundError(
                f"Neither base volume ({base_volume_path}) nor original mask ({original_mask_path}) found."
            )
        
        # Check if the raw bytes match the reference shape
        expected_voxels = int(np.prod(ref_shape))
        
        if actual_bytes == expected_voxels:
            dtype = np.uint8
        elif actual_bytes == expected_voxels * 2:
            dtype = np.uint16
        elif actual_bytes == expected_voxels * 4:
            dtype = np.float32
        else:
            # The frontend volume may have a different shape than the stored mask
            # (e.g., full 59 slices vs cropped 30 slices). 
            # Try common dtypes against the raw byte count.
            inferred_shape = None
            for try_dtype, bpp in [(np.uint8, 1), (np.uint16, 2), (np.float32, 4)]:
                total_voxels = actual_bytes // bpp
                if actual_bytes % bpp == 0 and total_voxels > 0:
                    # Assume X and Y match the reference, compute Z
                    if ref_shape[0] * ref_shape[1] > 0:
                        z_slices = total_voxels // (ref_shape[0] * ref_shape[1])
                        if z_slices * ref_shape[0] * ref_shape[1] == total_voxels and z_slices > 0:
                            inferred_shape = (ref_shape[0], ref_shape[1], z_slices)
                            dtype = try_dtype
                            logger.info(f"Inferred frontend volume shape: {inferred_shape} with dtype={try_dtype}")
                            break
            
            if inferred_shape is None:
                raise ValueError(
                    f"Size mismatch: reference shape {ref_shape} ({expected_voxels} voxels), "
                    f"received {actual_bytes} bytes. Could not infer frontend shape."
                )
            ref_shape = inferred_shape
        
        # Convert raw bytes to a flat array and reshape
        flat_array = np.frombuffer(raw_bytes, dtype=dtype)
        reshaped_array = flat_array.reshape(ref_shape, order='F')
        
        # Create a new NIfTI image with the modified data
        affine = ref_nii.affine if ref_nii is not None else np.eye(4)
        new_nii = nib.Nifti1Image(reshaped_array, affine)
        nib.save(new_nii, file_path)
        logger.info(f"Saved modified mask: {file_path}, shape={ref_shape}, dtype={dtype}")
        
    except Exception as e:
        logger.error(f"Failed to process and save modified mask: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to process mask: {str(e)}")
    
    return {
        "message": "Modified mask saved successfully", 
        "session_id": session_id,
        "path": os.path.abspath(file_path)
    }


@app.get("/segmentation-review/mask/{session_id}/{series_uid}")
async def get_segmentation_mask(session_id: str, series_uid: str):
    """
    Serve the agent-generated mask for a given session and series UID as an NRRD file.
    The mask is converted from NIfTI (.nii.gz) to NRRD on-the-fly so the existing
    frontend SegmentationReader.parseNrrdData() pipeline works without modification.

    Search order:
      1. temp_segmentations/ – user-uploaded modified masks (highest priority)
      2. masks/              – agent-generated masks
    """
    import io
    import numpy as np

    try:
        import nibabel as nib
        import nrrd
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"Required library missing: {e}")

    base_agents_workspace = os.environ.get("AGENTS_WORKSPACE_DIR", os.path.dirname(os.path.abspath(__file__)))
    workspace_folder = os.path.join(base_agents_workspace, f"workspace_{session_id}")

    # Search locations in priority order
    candidates = [
        os.path.join(workspace_folder, "temp_segmentations", f"seg_{series_uid}_modified.nii.gz"),
        os.path.join(workspace_folder, "masks", f"{series_uid}.nii.gz"),
    ]

    nifti_path = None
    for path in candidates:
        if os.path.isfile(path):
            nifti_path = path
            break

    if not nifti_path:
        raise HTTPException(
            status_code=404,
            detail=f"No mask found for series '{series_uid}' in session '{session_id}'"
        )

    logger.info(f"Serving mask (as NRRD) for session={session_id}, series={series_uid}: {nifti_path}")

    try:
        # Load NIfTI and convert to uint16 (matches what the frontend Uint16Array expects)
        nii = nib.load(nifti_path)
        data = nii.get_fdata().astype(np.uint16)

        # Build NRRD header from NIfTI metadata to preserve orientation
        affine = nii.affine.copy()
        
        # NIfTI uses RAS+ coordinates, NRRD here uses LPS+.
        # We negate the X and Y axes in the affine matrix to map correctly.
        affine[0, :] = -affine[0, :]
        affine[1, :] = -affine[1, :]
        
        header = {
            "type": "uint16",
            "dimension": 3,
            "space": "left-posterior-superior",
            "sizes": list(data.shape),
            "space directions": [
                [float(affine[0,0]), float(affine[1,0]), float(affine[2,0])],
                [float(affine[0,1]), float(affine[1,1]), float(affine[2,1])],
                [float(affine[0,2]), float(affine[1,2]), float(affine[2,2])],
            ],
            "space origin": [float(affine[0,3]), float(affine[1,3]), float(affine[2,3])],
            "kinds": ["domain", "domain", "domain"],
            "endian": "little",
            "encoding": "gzip",
        }

        # Serialize to NRRD bytes in memory
        buffer = io.BytesIO()
        nrrd.write(buffer, data, header)
        nrrd_bytes = buffer.getvalue()

        return Response(
            content=nrrd_bytes,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={series_uid}.nrrd"},
        )

    except Exception as e:
        logger.error(f"Failed to convert mask to NRRD: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to convert mask: {str(e)}")


@app.get("/segmentation-review/wait/{session_id}")
async def wait_for_review(session_id: str, timeout: int = 300):
    """Long-poll endpoint: blocks until the frontend submits a review."""
    # check if result already arrived
    ready_key = f"ready:{session_id}"
    if ready_key in _review_futures:
        result = _review_futures.pop(ready_key)
        return result

    # otherwise register a future and wait
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    _review_futures[session_id] = future

    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        _review_futures.pop(session_id, None)
        raise HTTPException(status_code=408, detail="Review timeout")

# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8150, reload=True)
