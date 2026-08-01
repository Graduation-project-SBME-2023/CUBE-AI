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
from services.session_service import (
    SessionExecutionConflict,
    SessionPlanningConflict,
    SessionService,
)
from services.pipeline_monitor import PipelineMonitor, raise_for_fatal_tool_failure
from clients.backend_client import BackendClient
from agents.execution_agents.segmentation.progressive import (
    artifact_key_for_pair,
    build_snapshot_from_manifest,
)

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
    original_workspace = os.path.join(
        base_agents_workspace, f"workspace_{session.session_id}"
    )
    # On restart, session.workspace_folder is set to a new retry folder.
    # On first run it is None, so we fall back to the original.
    workspace_folder = getattr(session, "workspace_folder", None) or original_workspace
    resume_from_agent = session.resume_from_agent
    resume_agent_statuses = dict(session.resume_agent_statuses)
    os.makedirs(workspace_folder, exist_ok=True)
    pipeline_logger = logging.getLogger("Execution Pipeline")

    logging.getLogger("autogen_core").setLevel(logging.CRITICAL)
    logging.getLogger("autogen_core.events").setLevel(logging.CRITICAL)

    # Load planner state: hypothesis + task messages (if available)
    # Always read from the original workspace — that's where the planner wrote state.json
    state_file = os.path.join(original_workspace, "state.json")
    task_messages = []
    hypothesis = None
    try:
        if os.path.exists(state_file):
            async with aiofiles.open(state_file, "r") as f:
                content = await f.read()
                if content.strip():
                    planner_state = PlannerState.model_validate_json(content)
                    task_messages = [step.task_message for step in planner_state.plan.steps if step.task_message is not None]
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

    from autogen_core import CancellationToken
    cancellation_token = CancellationToken()

    async def cancel_on_event():
        await session.cancel_event.wait()
        cancellation_token.cancel()

    cancel_task = asyncio.create_task(cancel_on_event())

    def make_pausable(client):
        original_create = client.create
        original_create_stream = client.create_stream
        
        async def wrapped_create(*args, **kwargs):
            await session.pause_event.wait()
            if session.cancel_event.is_set():
                raise asyncio.CancelledError()
            return await original_create(*args, **kwargs)
            
        async def wrapped_create_stream(*args, **kwargs):
            await session.pause_event.wait()
            if session.cancel_event.is_set():
                raise asyncio.CancelledError()
            async for chunk in original_create_stream(*args, **kwargs):
                yield chunk

        client.create = wrapped_create
        client.create_stream = wrapped_create_stream
        return client

    try:
        graph_builder = await BasicPipelineBuilder.create(
            workspace_folder=workspace_folder,
            task_messages=task_messages,
            model_client=make_pausable(create_model_client_from_config()),
            session_id=session.session_id,
            start_agent_id=resume_from_agent,
        )

        graph_flow = GraphFlow(
            participants=graph_builder.get_participants(),
            graph=graph_builder.build(),
        )


        # Build the pipeline prompt: use structured task messages if available,
        # otherwise fall back to the raw hypothesis string
        # task_prompt = graph_builder.get_pipeline_prompt(fallback_hypothesis=hypothesis)
        task_prompt = (
            graph_builder.get_pipeline_prompt(fallback_hypothesis=hypothesis)
            if resume_from_agent
            else hypothesis
        )
        pipeline_logger.info(f"Pipeline prompt length: {len(task_prompt)} chars")

        pipeline_logger.info("Starting execution...")

        session.execution_monitor = PipelineMonitor(
            session_service,
            session,
            initial_statuses=resume_agent_statuses,
        )

        await session.execution_monitor._broadcast()

        try:
            async for event in graph_flow.run_stream(task=task_prompt, cancellation_token=cancellation_token):
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
                            failed = await session.execution_monitor.on_tool_result(
                                src,
                                result.name,
                                result.content,
                                workspace_folder,
                                execution_error=bool(result.is_error),
                            )
                            pipeline_logger.info(
                                f"     Tool: {result.name}  Result: {result.content}"
                            )
                            raise_for_fatal_tool_failure(
                                src,
                                result.name,
                                failed,
                                result.content,
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
            raise

    except (asyncio.CancelledError, Exception) as _top_exc:
        # If the crash happened before execution_monitor was initialised (e.g. during
        # BasicPipelineBuilder.create()), there is nothing to notify via the monitor.
        # Broadcast run_stopped directly so the frontend knows execution ended.
        if not hasattr(session, 'execution_monitor') or session.execution_monitor is None:
            logger.error(f"Pipeline crashed before monitor init: {_top_exc}")
            await ws_manager.send_ws_message(
                "run_stopped",
                {"reason": "init_failure", "error": str(_top_exc)},
                session_id=session.session_id,
            )
        raise
    finally:
        cancel_task.cancel()


# ----------------------------------------------------------------------
# Routes — thin, all logic delegated to session_service
# ----------------------------------------------------------------------


def _clean_workspace_population_state(session_id: str) -> None:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        base_agents_workspace = os.environ.get("AGENTS_WORKSPACE_DIR", current_dir)
        workspace_folder = os.path.join(base_agents_workspace, f"workspace_{session_id}")
        if not os.path.exists(workspace_folder):
            return
        config_path = os.path.join(workspace_folder, "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data.pop("datasetlist", None)
                data["cohort_approved"] = False
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4)
            except Exception:
                pass
        labels_path = os.path.join(workspace_folder, "populations", "cohort_labels.csv")
        if os.path.exists(labels_path):
            os.remove(labels_path)
        targets_path = os.path.join(workspace_folder, "populations", "targets", "targets.csv")
        if os.path.exists(targets_path):
            os.remove(targets_path)
    except Exception as e:
        logger.warning(f"Failed to clean workspace population state for session {session_id}: {e}")


@app.post("/plan")
async def start_planning(request: PlanningRequest):
    _clean_workspace_population_state(request.session_id)
    from functools import partial
    bound_planner = partial(run_planner, do_lit_review=request.do_lit_review)
    try:
        await session_service.start_planning(
            request.session_id,
            request.message,
            bound_planner,
            restart=request.restart,
        )
    except SessionPlanningConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"session_id": request.session_id, "status": "planning_started"}


@app.post("/run")
async def start_execution(request: SessionControlRequest):
    try:
        await session_service.start_execution(
            request.session_id,
            run_pipeline,
            restart=request.restart,
        )
    except SessionExecutionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    # Immediately tell the UI that execution has started so tabs unlock
    # without waiting for the first pipeline_agent_status message.
    await ws_manager.send_ws_message(
        "run_status",
        {"state": "RUNNING", "current_agent": None},
        session_id=request.session_id,
    )
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


@app.post("/session/{session_id}/resume-pipeline")
async def resume_hydrated_pipeline(session_id: str):
    async def reset_runtime_state(
        target_session_id: str,
        interrupted_agent: str,
    ) -> None:
        if interrupted_agent != "segmentation_agent":
            return
        _review_futures.pop(target_session_id, None)
        _review_futures.pop(f"ready:{target_session_id}", None)
        _review_events.pop(target_session_id, None)

    try:
        return await session_service.resume_pipeline(
            session_id,
            run_pipeline,
            workspace_root=os.environ.get("AGENTS_WORKSPACE_DIR", current_dir),
            reset_runtime_state=reset_runtime_state,
        )
    except SessionExecutionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/stop")
async def stop_execution(request: SessionControlRequest):
    try:
        session = session_service.store.require(request.session_id)

        session_service.stop(request.session_id)

        # Bug fix #1: broadcast run_stopped so the frontend resets its mode
        asyncio.create_task(
            ws_manager.send_ws_message("run_stopped", "stopped", session_id=request.session_id)
        )

        return {"session_id": request.session_id, "status": "stopped"}
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")



@app.get("/status/{session_id}")
async def get_status(session_id: str):
    session = session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session_id,
        "state": session.state.name,
        "last_error": session.last_error,
        "execution_active": bool(
            session.execution_task and not session.execution_task.done()
        ),
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

    workspace_root = os.environ.get(
        "AGENTS_WORKSPACE_DIR",
        os.path.abspath(os.path.join(current_dir, "..", "agents_workspace")),
    )
    workspace = os.path.join(workspace_root, f"workspace_{session_id}")
    manifest_path = os.path.join(workspace, "segmentation_manifest.json")
    try:
        with open(manifest_path, "r", encoding="utf-8") as stream:
            manifest = json.load(stream)
        if manifest.get("stage") == "awaiting_review":
            snapshot = build_snapshot_from_manifest(
                manifest,
                workspace,
                os.environ.get("MAS_BASE_URL", "http://localhost:8150"),
            )
            if snapshot:
                await ws_manager.send_ws_message(
                    "segmentation_output",
                    snapshot,
                    session_id,
                    replaying=True,
                )
    except FileNotFoundError:
        pass
    except (OSError, ValueError, json.JSONDecodeError):
        logger.warning(
            "Could not reconstruct segmentation snapshot for session %s",
            session_id,
            exc_info=True,
        )



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
            # NOTE: Do NOT push_to_queue here — that caused every message to be
            # queued twice (once as raw text, once after JSON parsing).
            # Only queue inside the typed message block below.
            logging.getLogger("HypothesisFlow").info(
                "[HypothesisFlow][WS] Message received raw | size=%s",
                len(data or ""),
            )

            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                # Backward compatibility: plain text is treated as user input.
                # Queued exactly ONCE here.
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
    if data.get("type") == "agent_waiting_for_user_input":
        payload = data.get("payload") or {}
        session_id = data.get("session_id")
        agent_name = payload.get("agent")
        waiting_status = payload.get("status")
        if session_id and agent_name and waiting_status:
            await session_service.persist_agent_status(
                str(session_id),
                str(agent_name),
                str(waiting_status),
            )
    await ws_manager.send_ws_message(
        data["type"], data["payload"], session_id=data.get("session_id")
    )
    return {"status": "ok"}
    
# ── Review long-poll state ──────────────────────────────────────────────────
# Keyed by session_id.  Each entry is a dict with two keys:
#   "event"  – asyncio.Event set when a review arrives
#   "result" – the review payload dict (populated before event.set())
# A separate "ready:{session_id}" key stores a plain dict for reviews that
# arrived before the module started polling (unchanged behaviour).
_review_futures: Dict[str, asyncio.Future] = {}  # kept for ready-prefix entries
_review_events: Dict[str, dict] = {}  # session_id -> {"event": Event, "result": dict|None}


# ── Review disk-persistence helpers ──────────────────────────────────────────
# Saves/loads the human-review result to disk so it survives Docker restarts.
# File: <workspace>/review_result.json

def _review_disk_path(session_id: str) -> str:
    base = os.environ.get("AGENTS_WORKSPACE_DIR", "/app/agents_workspace")
    workspace = os.path.join(base, f"workspace_{session_id}")
    return os.path.join(workspace, "review_result.json")


def _persist_review_to_disk(session_id: str, payload: dict) -> None:
    """Write the review payload to disk so it survives a container restart."""
    try:
        path = _review_disk_path(session_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        logger.info(f"[Review] Persisted review to disk for session={session_id}: {path}")
    except Exception as exc:
        logger.error(f"[Review] Failed to persist review to disk for session={session_id}: {exc}")


def _load_review_from_disk(session_id: str) -> dict | None:
    """Load a persisted review result from disk (returns None if not found)."""
    try:
        path = _review_disk_path(session_id)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        logger.info(f"[Review] Loaded review from disk for session={session_id}: {path}")
        return payload
    except Exception as exc:
        logger.error(f"[Review] Failed to load review from disk for session={session_id}: {exc}")
        return None


def _clear_review_from_disk(session_id: str) -> None:
    """Delete the persisted review file once it has been consumed."""
    try:
        path = _review_disk_path(session_id)
        if os.path.isfile(path):
            os.remove(path)
            logger.info(f"[Review] Cleared disk review for session={session_id}")
    except Exception as exc:
        logger.warning(f"[Review] Failed to clear disk review for session={session_id}: {exc}")

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





# ----------------------------------------------------------------------
# Cohort Management Endpoints (Interactive Subpages)
# ----------------------------------------------------------------------

@app.get("/populations/cohort-state")
async def get_cohort_state(
    session_id: str = Query(..., description="The ID of the current session"),
):
    """
    Returns the current cohort state for the interactive Population subpages:
    - datasetlist from config.json
    - labels from cohort_labels.csv
    - cohort_approved flag
    - labelling_mode
    """
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

    # Load cohort labels if available
    import csv as csv_mod
    labels = []
    labels_path = os.path.join(workspace_folder, "populations", "cohort_labels.csv")
    if os.path.exists(labels_path):
        try:
            with open(labels_path, "r", encoding="utf-8") as f:
                reader = csv_mod.DictReader(f)
                for row in reader:
                    labels.append(dict(row))
        except Exception:
            pass

    return {
        "datasetlist": config_data.get("datasetlist", []),
        "labelling_mode": config_data.get("labelling_mode", "automatic"),
        "cohort_approved": config_data.get("cohort_approved", False),
        "inclusion_criteria": config_data.get("inclusion_criteria", []),
        "exclusion_criteria": config_data.get("exclusion_criteria", []),
        "labels": labels,
    }


@app.post("/populations/update-cohort-list")
async def update_cohort_list(
    data: dict,
):
    """
    Updates the datasetlist in config.json with the user's include/exclude
    selections from the Data Browser subpage.
    Expects: { "session_id": "...", "datasetlist": ["uid1", "uid2", ...] }
    """
    session_id = data.get("session_id")
    new_datasetlist = data.get("datasetlist", [])

    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

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

    config_data["datasetlist"] = new_datasetlist
    # Reset approval when the cohort list changes
    config_data["cohort_approved"] = False

    os.makedirs(workspace_folder, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4)

    # Broadcast the update so the Overview subpage refreshes
    try:
        await ws_manager.send_ws_message(
            "population_output",
            {
                "tool": "search_volumes_by_description",
                "status": "completed",
                "count": len(new_datasetlist),
                "uid_list": new_datasetlist,
            },
            session_id=session_id,
        )
    except Exception as e:
        logger.warning(f"Failed to broadcast cohort list update: {e}")

    return {"status": "success", "count": len(new_datasetlist)}


@app.post("/populations/save-labels")
async def save_labels(data: dict):
    """
    Saves user label assignments from the Patient Labeling subpage.
    Expects: {
        "session_id": "...",
        "labels": [
            {"patient_id": "...", "has_target_disease": "true"/"false"/"",
             "confidence": "...", "source_evidence": "..."},
            ...
        ]
    }
    """
    import csv as csv_mod

    session_id = data.get("session_id")
    labels_list = data.get("labels", [])

    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    current_dir = os.path.dirname(os.path.abspath(__file__))
    base_agents_workspace = os.environ.get("AGENTS_WORKSPACE_DIR", current_dir)
    workspace_folder = os.path.join(base_agents_workspace, f"workspace_{session_id}")
    populations_dir = os.path.join(workspace_folder, "populations")
    os.makedirs(populations_dir, exist_ok=True)
    labels_path = os.path.join(populations_dir, "cohort_labels.csv")

    # Reset approval when labels change
    config_path = os.path.join(workspace_folder, "config.json")
    config_data = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except Exception:
            pass
    config_data["cohort_approved"] = False
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4)

    fieldnames = [
        "patient_id", "has_target_disease", "confidence",
        "requires_manual_review", "source_evidence"
    ]
    with open(labels_path, "w", newline="", encoding="utf-8") as f:
        writer = csv_mod.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for label in labels_list:
            writer.writerow({
                "patient_id": label.get("patient_id", ""),
                "has_target_disease": label.get("has_target_disease", ""),
                "confidence": label.get("confidence", ""),
                "requires_manual_review": label.get("requires_manual_review", "False"),
                "source_evidence": label.get("source_evidence", ""),
            })

    return {"status": "success", "saved_count": len(labels_list)}


@app.post("/populations/approve-cohort")
async def approve_cohort(data: dict):
    """
    Validates and approves the final cohort. The pipeline will only proceed
    to segmentation after this endpoint succeeds.
    Expects: { "session_id": "..." }
    Validates: At least one volume in the datasetlist has a valid label.
    """
    import csv as csv_mod

    session_id = data.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

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

    datasetlist = config_data.get("datasetlist", [])
    if not datasetlist:
        raise HTTPException(
            status_code=400,
            detail="Cannot approve: no volumes in the cohort. The population agent must discover volumes first."
        )

    # Load labels and check that at least one volume has a valid label
    labels_path = os.path.join(workspace_folder, "populations", "cohort_labels.csv")
    labels_dict = {}
    if os.path.exists(labels_path):
        try:
            with open(labels_path, "r", encoding="utf-8") as f:
                reader = csv_mod.DictReader(f)
                for row in reader:
                    labels_dict[row.get("patient_id", "")] = row
        except Exception:
            pass

    # Count volumes with valid labels (has_target_disease is "true" or "false", not empty)
    labeled_count = 0
    valid_targets = []
    for uid in datasetlist:
        label_info = labels_dict.get(uid)
        if label_info:
            htd = str(label_info.get("has_target_disease", "")).strip().lower()
            if htd in ("true", "false"):
                labeled_count += 1
                target_val = 1 if htd == "true" else 0
                valid_targets.append((uid, label_info.get("patient_id", uid), target_val))

    if labeled_count == 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot approve: no volumes have valid labels. Please label at least one volume in the Patient Labeling tab."
        )

    # Generate the sealed targets.csv
    targets_dir = os.path.join(workspace_folder, "populations", "targets")
    os.makedirs(targets_dir, exist_ok=True)
    targets_path = os.path.join(targets_dir, "targets.csv")

    with open(targets_path, "w", newline="", encoding="utf-8") as f:
        writer = csv_mod.writer(f)
        writer.writerow(["id", "name", "target"])
        for uid, name, target in valid_targets:
            writer.writerow([uid, name, target])

    # Update config: only labeled UIDs in the final datasetlist, mark approved
    config_data["datasetlist"] = [uid for uid, _, _ in valid_targets]
    config_data["cohort_approved"] = True
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4)

    logger.info(f"[CohortApproval] Session {session_id}: approved {labeled_count} labeled volumes out of {len(datasetlist)} total.")

    # Broadcast approval so the pipeline can proceed
    try:
        await ws_manager.send_ws_message(
            "cohort_approval_status",
            {
                "status": "approved",
                "labeled_count": labeled_count,
                "total_count": len(datasetlist),
            },
            session_id=session_id,
        )
    except Exception as e:
        logger.warning(f"Failed to broadcast cohort approval: {e}")

    return {
        "status": "success",
        "labeled_count": labeled_count,
        "targets_path": targets_path,
    }


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

    active_session = session_service.get_session(session_id)
    if (
        active_session
        and active_session.execution_task
        and not active_session.execution_task.done()
    ):
        await session_service.persist_agent_status(
            session_id,
            "study_population_planner_agent",
            "RUNNING",
        )
    await ws_manager.send_ws_message(
        "agent_waiting_resolved",
        {"agent": "study_population_planner_agent"},
        session_id=session_id,
    )

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

    # Always persist to disk first so the result survives container restarts.
    _persist_review_to_disk(session_id, review_payload)

    # Resolve the waiting module if it's already polling via the event mechanism.
    ev_entry = _review_events.get(session_id)
    if ev_entry is not None:
        ev_entry["result"] = review_payload
        ev_entry["event"].set()
        logger.info(f"[Review] Resolved waiting event for session={session_id}")
    else:
        # Store in memory so the module can pick it up if it reconnects before restart.
        _review_futures[f"ready:{session_id}"] = review_payload  # plain dict, not future
        logger.info(f"[Review] No active poller for session={session_id}. Stored as ready:{session_id} for later pickup.")

    active_session = session_service.get_session(session_id)
    if (
        active_session
        and active_session.execution_task
        and not active_session.execution_task.done()
    ):
        await session_service.persist_agent_status(
            session_id,
            "segmentation_agent",
            "RUNNING",
        )
    await ws_manager.send_ws_message(
        "agent_waiting_resolved",
        {"agent": "segmentation_agent"},
        session_id=session_id,
    )

    return {"status": "ok"}


@app.post("/segmentation-review/upload-mask")
async def upload_modified_mask(
    request: Request,
    session_id: str = Form(...),
    series_instance_uid: str = Form(...),
    target_id: str | None = Form(None),
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
    
    pair_id = (
        f"{series_instance_uid}__{target_id}"
        if target_id
        else series_instance_uid
    )
    artifact_key = artifact_key_for_pair(pair_id)
    file_path = os.path.join(output_dir, f"seg_{artifact_key}_modified.nii.gz")
    # The frontend mask matches the full DICOM volume space.
    # The workspace stores volumes in 'volumes/' and masks in 'masks/'.
    base_volume_path = os.path.join(workspace_folder, "volumes", f"{series_instance_uid}.nii.gz")
    original_mask_path = os.path.join(
        workspace_folder, "masks", f"{artifact_key}.nii.gz"
    )
    legacy_pair_name_is_safe = "/" not in pair_id and "\\" not in pair_id
    if not os.path.exists(original_mask_path) and legacy_pair_name_is_safe:
        original_mask_path = os.path.join(
            workspace_folder, "masks", f"{pair_id}.nii.gz"
        )
    
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
    
    from agents.execution_agents.segmentation.utils import generate_gif_overlays

    overlay_results = await asyncio.to_thread(
        generate_gif_overlays,
        workspace_folder,
        [
            {
                "pair_id": pair_id,
                "artifact_key": artifact_key,
                "volume_uid": series_instance_uid,
                "target_id": target_id or "",
                "mask_path": file_path,
            }
        ],
    )
    if not overlay_results:
        raise HTTPException(
            status_code=500,
            detail="Modified mask was saved, but its preview could not be regenerated.",
        )

    overlay = overlay_results[0]
    gif_path = str(overlay.get("gifPath") or f"overlays/{artifact_key}.gif")
    overlay["gifUrl"] = (
        f"{str(request.base_url).rstrip('/')}/files/"
        f"{os.path.basename(workspace_folder)}/{gif_path}"
    )
    overlay["updatedAt"] = os.stat(
        os.path.join(workspace_folder, *gif_path.split("/"))
    ).st_mtime_ns

    return {
        "message": "Modified mask saved successfully", 
        "session_id": session_id,
        "path": os.path.abspath(file_path),
        "status": "saved",
        "overlay": overlay,
    }


@app.get("/segmentation-review/mask/{session_id}/{series_uid}")
async def get_segmentation_mask(
    session_id: str,
    series_uid: str,
    target_id: str | None = Query(None),
):
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
    pair_id = f"{series_uid}__{target_id}" if target_id else series_uid
    artifact_key = artifact_key_for_pair(pair_id)
    candidates = [
        os.path.join(workspace_folder, "temp_segmentations", f"seg_{artifact_key}_modified.nii.gz"),
        os.path.join(workspace_folder, "masks", f"{artifact_key}.nii.gz"),
    ]
    if "/" not in pair_id and "\\" not in pair_id:
        candidates.extend(
            [
                os.path.join(
                    workspace_folder,
                    "temp_segmentations",
                    f"seg_{pair_id}_modified.nii.gz",
                ),
                os.path.join(workspace_folder, "masks", f"{pair_id}.nii.gz"),
            ]
        )

    nifti_path = None
    for path in candidates:
        if os.path.isfile(path):
            nifti_path = path
            break

    # Failed or rejected segmentations may still be advertised in the QC grid
    # even though cleanup removed their mask file. Keep those entries editable
    # by initializing an empty mask from the source volume geometry.
    is_empty_fallback = False
    if not nifti_path:
        volume_path = os.path.join(
            workspace_folder, "volumes", f"{series_uid}.nii.gz"
        )
        if target_id and os.path.isfile(volume_path):
            nifti_path = volume_path
            is_empty_fallback = True
            logger.warning(
                "Mask missing for session=%s, pair=%s; serving an empty editable "
                "mask using source volume geometry: %s",
                session_id,
                pair_id,
                volume_path,
            )
        else:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No mask or source volume found for series '{series_uid}' "
                    f"in session '{session_id}'"
                ),
            )

    logger.info(
        "Serving %smask (as NRRD) for session=%s, series=%s, target=%s: %s",
        "empty editable " if is_empty_fallback else "",
        session_id,
        series_uid,
        target_id or "",
        nifti_path,
    )

    try:
        # Load NIfTI and convert to uint16 (matches what the frontend Uint16Array expects)
        nii = nib.load(nifti_path)
        data = (
            np.zeros(nii.shape[:3], dtype=np.uint16)
            if is_empty_fallback
            else nii.get_fdata().astype(np.uint16)
        )

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
            headers={
                "Content-Disposition": f"attachment; filename={pair_id}.nrrd",
                "Cache-Control": "no-store",
                "X-Segmentation-Empty-Fallback": (
                    "true" if is_empty_fallback else "false"
                ),
            },
        )

    except Exception as e:
        logger.error(f"Failed to convert mask to NRRD: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to convert mask: {str(e)}")


@app.get("/segmentation-review/wait/{session_id}")
async def wait_for_review(session_id: str, timeout: int = 3600):
    """Long-poll endpoint: blocks until the frontend submits a review.

    Uses a persistent asyncio.Event so that reconnecting clients re-attach to
    the *same* event rather than creating a new Future every time.  This means
    a review submitted during a brief reconnect window is never lost.
    """
    # ── Fast path: review already arrived before this poll ───────────────────
    ready_key = f"ready:{session_id}"
    # Also check disk so review results survive Docker restarts.
    if ready_key not in _review_futures:
        disk_result = _load_review_from_disk(session_id)
        if disk_result is not None:
            _review_futures[ready_key] = disk_result
    if ready_key in _review_futures:
        result = _review_futures.pop(ready_key)
        # clean up event entry and disk file if present
        _review_events.pop(session_id, None)
        _clear_review_from_disk(session_id)
        return result

    # ── Also check the event store (review arrived while another poll was alive)
    ev_entry = _review_events.get(session_id)
    if ev_entry is not None and ev_entry["event"].is_set():
        result = ev_entry["result"]
        _review_events.pop(session_id, None)
        _clear_review_from_disk(session_id)   # clean up disk file here too
        return result

    # ── Register / reuse a persistent event for this session ─────────────────
    if session_id not in _review_events:
        _review_events[session_id] = {"event": asyncio.Event(), "result": None}
    ev_entry = _review_events[session_id]

    try:
        await asyncio.wait_for(asyncio.shield(ev_entry["event"].wait()), timeout=timeout)
    except asyncio.TimeoutError:
        # Connection timed out — return 408 so the client reconnects, but
        # intentionally keep _review_events[session_id] alive so the next
        # connection reuses the same event and result slot.
        logger.debug(f"[Review] Long-poll timeout for session={session_id}. Client will reconnect.")
        raise HTTPException(status_code=408, detail="Review timeout")

    # Event was set — collect result and clean up
    result = ev_entry["result"]
    _review_events.pop(session_id, None)
    _clear_review_from_disk(session_id)
    return result

# ----------------------------------------------------------------------
# Session Recovery
# ----------------------------------------------------------------------


@app.post("/session/{session_id}/recover")
async def recover_session(session_id: str, restart: bool = False):
    """
    Re-create an in-memory Session from persisted state after a process restart.

    Hydration is read-only by default. A new pipeline is created only when the
    caller explicitly passes ``restart=true``.
    """
    existing = session_service.get_session(session_id)
    if existing and existing.execution_task and not existing.execution_task.done():
        logger.info(
            f"[Recover] Session {session_id} is already running - skipping recovery."
        )
        return {
            "session_id": session_id,
            "status": "already_running",
            "state": existing.state.name,
        }

    session = await session_service.store.get_or_create_async(session_id)
    execution_state = await session_service.recover_execution_state(session_id)
    if execution_state is None:
        raise HTTPException(status_code=404, detail="No persisted session state found")

    if not restart:
        logger.info(
            f"[Recover] Hydrated session {session_id} as {session.state.name}; "
            "execution was not started."
        )
        return {
            "session_id": session_id,
            "status": "hydrated",
            "state": session.state.name,
            "execution_state": execution_state,
        }

    await session_service.start_execution(session_id, run_pipeline, restart=True)
    return {"session_id": session_id, "status": "restart_started"}


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8150, reload=True)
