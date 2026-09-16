import os
import sys

# Make Backend/ importable (storage_paths lives there) before any local import.
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, ".."))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import asyncio
from contextlib import asynccontextmanager

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

from storage_paths import (
    HYPOTHESES_DIR,
    TEMP_DIR,
    ensure_storage_layout,
    workspace,
    workspace_subpath,
    ws_sub,
)

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
    save_config_to_disk,
)

from models.tool_logging import ToolEvent
from models.requests import PlanningRequest, SessionControlRequest
from tool_logging import receive_tool_event
from services.session_service import (
    SessionExecutionConflict,
    SessionPlanningConflict,
    SessionService,
    load_runs,
)
from services.run_context import agent_type_of
from services.best_effort import drain_best_effort, initialize_best_effort
from services.pipeline_monitor import PipelineMonitor, raise_for_fatal_tool_failure
from services.population_handoff import require_population_handoff
from services import population_review
from services.artifact_client import register_render, render_logical_key
from clients.backend_client import BackendClient
from agents.execution_agents.segmentation.progressive import (
    artifact_key_for_pair,
    build_snapshot_from_manifest,
)
from agents.execution_agents.segmentation.services import SegmentationService

logger = logging.getLogger(__name__)

class RedactApiKeyFilter(logging.Filter):
    def filter(self, record):
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            import re
            record.msg = re.sub(r'\'api_key\': \'[^\']*\'', "'api_key': '***'", record.msg)
            record.msg = re.sub(r'"api_key": "[^"]*"', '"api_key": "***"', record.msg)
        return True

logging.getLogger().addFilter(RedactApiKeyFilter())

logging.getLogger("watchfiles").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
# ----------------------------------------------------------------------
# App setup
# ----------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_best_effort()
    try:
        yield
    finally:
        await drain_best_effort(timeout_seconds=2.0)


app = FastAPI(
    title="MAS Orchestrator API",
    description="Receive a clinical hypothesis, run the Planner → Executor pipeline.",
    version="0.1.0",
    lifespan=lifespan,
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

ensure_storage_layout()
# A write to a retired workspace file raises from here on.
from storage_write_guard import install_write_guard

install_write_guard()
agents_workspace_dir = str(HYPOTHESES_DIR)


@app.get("/files/{workspace_name}/{logical_path:path}", name="legacy_workspace_file")
def serve_workspace_file(workspace_name: str, logical_path: str):
    """Serve legacy logical workspace URLs from the categorized layout."""
    if not workspace_name.startswith("workspace_"):
        raise HTTPException(status_code=404, detail="Workspace not found")

    first = logical_path.replace("\\", "/").split("/", 1)[0]
    allowed = {
        "reports", "overlays", "features", "modeling", "outputs",
        "populations", "volumes", "masks", "data",
    }
    if first not in allowed:
        raise HTTPException(status_code=404, detail="Artifact category not found")

    workspace_root = workspace(workspace_name)
    normalized = logical_path.replace("\\", "/").strip("/")
    physical_prefixes = (
        "outputs/agents/", "outputs/literature/", "outputs/overlays/",
        "outputs/features/", "outputs/modeling/", "outputs/reports/",
        "data/populations/", "data/volumes/", "data/masks/",
    )
    if normalized.startswith(physical_prefixes):
        target = (workspace_root / normalized).resolve()
    else:
        try:
            target = workspace_subpath(workspace_root, normalized)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="Invalid artifact path") from exc

    try:
        target.relative_to(workspace_root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Artifact path escapes workspace") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(target)


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
    workspace_folder = str(workspace(session.session_id))
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
    try:
        await planner.run(start_query=message, do_lit_review=do_lit_review)
        logging.getLogger("HypothesisFlow").info(
            "[HypothesisFlow][Planner] Planner finished | session_id=%s",
            session.session_id,
        )
    finally:
        planner.close()


# ----------------------------------------------------------------------
# Executor callable — receives session, calls back into service
# ----------------------------------------------------------------------


async def run_pipeline(session: Session) -> None:
    """Execute the session's approved plan."""
    workspace_folder = str(workspace(session.session_id))
    os.makedirs(workspace_folder, exist_ok=True)
    pipeline_logger = logging.getLogger("Execution Pipeline")

    logging.getLogger("autogen_core").setLevel(logging.CRITICAL)
    logging.getLogger("autogen_core.events").setLevel(logging.CRITICAL)

    runs = await load_runs(session.session_id)
    if not runs:
        raise RuntimeError(
            f"Session {session.session_id} has no approved agent runs to execute"
        )

    resume_from_step_id = session.resume_from_step_id
    session_row = await BackendClient.get_session(session.session_id) or {}
    # The user's own words, which current keeps instead of overwriting (defect 1).
    prompt = session_row.get("prompt") or ""

    pipeline_logger.info(
        "workspace_folder: %s, steps: %d, resume_from_step: %s",
        workspace_folder,
        len(runs),
        resume_from_step_id or "-",
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

    # Node name -> agent_run_id, so a contract is checked on the run that
    # produced it rather than on "the" agent.
    run_by_node = {run.node_name: run.agent_run_id for run in runs}

    try:
        session.execution_monitor = PipelineMonitor(session_service, session, runs=runs)

        graph_builder = await BasicPipelineBuilder.create(
            workspace_folder=workspace_folder,
            runs=runs,
            model_client=make_pausable(create_model_client_from_config()),
            session_id=session.session_id,
            start_step_id=resume_from_step_id,
            on_start=lambda run: session_service.claim_run(session, run),
            on_finish=lambda run: session_service.finish_run(session, run),
            on_failure=lambda run, error: session_service.fail_run(session, run, error),
        )

        graph_flow = GraphFlow(
            participants=graph_builder.get_participants(),
            graph=graph_builder.build(),
        )

        task_prompt = graph_builder.get_pipeline_prompt(fallback_hypothesis=prompt)
        pipeline_logger.info(f"Pipeline prompt length: {len(task_prompt)} chars")
        pipeline_logger.info("Starting execution...")

        status = await BackendClient.get_agent_run_status(session.session_id)
        if status is not None:
            await ws_manager.send_ws_message(
                "pipeline_agent_status", status, session_id=session.session_id
            )

        try:
            async for event in graph_flow.run_stream(task=task_prompt, cancellation_token=cancellation_token):
                if session.cancel_event.is_set():
                    raise asyncio.CancelledError()
                await session.pause_event.wait()

                match event:
                    case TaskResult(messages=msgs):
                        pipeline_logger.info("=" * 60)
                        pipeline_logger.info("TASK COMPLETE")
                        for msg in msgs:
                            pipeline_logger.info(f"  [{msg.source}] {msg.content}")
                        pipeline_logger.info("=" * 60)
                        await session.execution_monitor.on_pipeline_complete()
                        await ws_manager.send_ws_message(
                            "run_finished", "run_finished", session_id=session.session_id
                        )

                    case ToolCallRequestEvent(source=src, content=content):
                        pipeline_logger.info(f"[{src}] TOOL CALL REQUEST")
                        await session.execution_monitor.on_agent_active(src)
                        for call in content:
                            pipeline_logger.info(
                                f"     Tool: {call.name}  Args: {call.arguments}"
                            )

                    case ToolCallExecutionEvent(source=src, content=content):
                        pipeline_logger.info(f"[{src}] TOOL CALL RESULT")
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
                                agent_type_of(src),
                                result.name,
                                failed,
                                result.content,
                            )

                    case MemoryQueryEvent(source=src, content=content):
                        pipeline_logger.info(f"[{src}] MEMORY QUERY: {content}")
                        await session.execution_monitor.on_agent_active(src)

                    case UserInputRequestedEvent(source=src):
                        pipeline_logger.info(f"[{src}] WAITING FOR USER INPUT")

                    case ThoughtEvent(source=src, content=content):
                        pipeline_logger.info(f"[{src}] THOUGHT: {content}")
                        await session.execution_monitor.on_agent_active(src)

                    case BaseChatMessage(source=src):
                        if agent_type_of(src) == "study_population_planner_agent":
                            await require_population_handoff(
                                session.session_id,
                                run_by_node.get(src),
                            )
                        pipeline_logger.info(
                            f"[{src}] ({type(event).__name__}): {event.content}"
                        )
                        await session.execution_monitor.on_agent_active(src)

                    case BaseAgentEvent(source=src):
                        pipeline_logger.info(f"[{src}] ({type(event).__name__})")
                        if hasattr(event, "content") and event.content:
                            pipeline_logger.info(f"   {event.content}")
                        await session.execution_monitor.on_agent_active(src)

        except asyncio.CancelledError:
            cancellation_token.cancel()
            # A cancelled pipeline is interrupted, not failed: the interrupted
            # run stays `running` so a resume continues the same run.
            await session.execution_monitor.on_pipeline_error(
                Exception("Pipeline Cancelled")
            )
            raise
        except Exception as e:
            # A consumer-side error does not automatically stop GraphFlow's
            # participant tasks. Propagate every terminal error immediately.
            cancellation_token.cancel()
            logger.exception("Execution pipeline failed")
            await session.execution_monitor.on_pipeline_error(e)
            raise

    except (asyncio.CancelledError, Exception) as _top_exc:
        # A crash before the monitor exists (graph construction, for example)
        # belongs to no run; tell the frontend the run ended.
        if getattr(session, "execution_monitor", None) is None:
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


@app.post("/plan")
async def start_planning(request: PlanningRequest):
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
    """Approve the plan and execute it."""
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
    """Continue an interrupted pipeline. **Deletes nothing**."""

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
        # Per-step progress is durable now: it comes from `agent_runs`, not
        # from what this process remembers.
        "runs": await BackendClient.get_agent_run_status(session_id),
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
        data["api_key"] = "***"
        data["_masked"] = True
    else:
        data["_masked"] = False
    return {"status": "ok", "config": data}


@app.post("/client-config")
async def update_config(payload: dict):
    """Update the global LLM client configuration with partial merge."""
    try:
        current_dict = get_client_config().model_dump()
        
        for key, value in payload.items():
            if key == "api_key" and value is None:
                current_dict["api_key"] = None
            elif key == "api_key" and (value == "" or value == "***"):
                continue
            else:
                current_dict[key] = value
                
        validated = ClientConfig(**current_dict)
        set_client_config(validated)
        save_config_to_disk(validated)
        
        masked = validated.model_dump()
        if masked.get("api_key"):
            masked["api_key"] = "***"
            masked["_masked"] = True
        else:
            masked["_masked"] = False
        return {"status": "ok", "config": masked}
    except Exception as e:
        logger.error(f"Error updating config: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/test-client-config")
async def test_client_config(payload: dict = None):
    try:
        if payload:
            current = get_client_config().model_dump()
            for key, value in payload.items():
                if key == "api_key" and (value == "" or value == "***"):
                    continue
                if value is not None:
                    current[key] = value
            config = ClientConfig(**current)
        else:
            config = get_client_config()
            
        if not config.provider or not config.model:
            raise ValueError("Model configuration incomplete. Please set provider and model.")
            
        client = create_model_client_from_config(config)
        # We need an async block to test connection.
        from autogen_core.models import UserMessage
        # Send a simple test message
        response = await client.create(messages=[UserMessage(content="Respond with exactly the word OK.", source="user")])
        
        return {"status": "ok", "message": "✅ Connection successful! Model is ready."}
    except Exception as e:
        logger.error(f"Error testing client config: {e}")
        return JSONResponse(status_code=400, content={"status": "error", "message": f"❌ Connection failed: {str(e)}"})


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
async def replay_logs(session_id: str, after_event_id: int = 0):
    """Replay a session's events from a cursor."""
    logs: list[WS_Log] = await BackendClient.replay_logs(
        session_id, after_event_id=after_event_id
    )
    last_event_id = after_event_id
    for log in logs:
        last_event_id = log.event_id or last_event_id
        if log.msg_type in ('msg'):
            continue
        await ws_manager.send_ws_message(
            log.msg_type,
            log.payload,
            log.session_id,
            replaying=True,
            event_id=log.event_id,
        )

    if after_event_id == 0 and not logs:
        # Nothing has ever happened in this session (fresh create/open) — no
        # agent has run yet, so there is no segmentation batch to resume a
        # review for. Skip the DB round trip entirely rather than always
        # probing it on every session open.
        return

    workspace_dir = str(workspace(session_id))
    try:
        batch = await population_review.current_batch(session_id)
        if batch and batch["review_status"] != "not_ready":
            # current this flow: rebuilt from segmentation_runs/pairs, including the
            # doctor's draft decisions, which are rows now.
            manifest = population_review.snapshot_manifest(batch)
            snapshot = build_snapshot_from_manifest(
                manifest,
                workspace_dir,
                os.environ.get("MAS_BASE_URL", "http://localhost:8150"),
            )
            if snapshot:
                await ws_manager.send_ws_message(
                    "segmentation_output",
                    snapshot,
                    session_id,
                    replaying=True,
                )
    except (population_review.DomainError, httpx.HTTPError, ValueError, KeyError):
        logger.warning(
            "Could not reconstruct segmentation snapshot for session %s",
            session_id,
            exc_info=True,
        )



@app.get("/session/{session_id}/config")
async def get_session_config(session_id: str):
    """The session's population configuration — read from its cohort."""
    try:
        cohort = await population_review.get_cohort(session_id)
    except population_review.DomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return population_review.cohort_state(cohort)

@app.get("/session/{session_id}/plan-state")
async def get_plan_state(session_id: str):
    """
    Get the session's hypothesis for recovery from history.

    PostgreSQL is the source of truth (sessions.intent); there is no
    workspace file to read. `task_messages` was never populated by any
    writer and is kept in the response only for API-shape compatibility.
    """
    hypothesis = await BackendClient.get_intent(session_id)
    if hypothesis is None:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id": session_id,
        "hypothesis": hypothesis.model_dump(),
        "task_messages": [],
    }


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
        monai_url = os.environ.get(
            "APP_API_URL",
            os.environ.get("CONTROL_API_URL", "http://localhost:8001"),
        )
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
    """Compile LaTeX text into a PDF inside the session workspace and return a public URL."""
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
    workspace_folder = str(workspace(session_id))
    reports_folder = str(workspace_subpath(workspace_folder, "reports"))
    os.makedirs(reports_folder, exist_ok=True)

    latex_temp_root = TEMP_DIR / "latex"
    latex_temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=latex_temp_root) as temp_dir:
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
    
# ── Review wake-ups ──────────────────────────────────────────────────────────
# The review itself is a row (segmentation_runs.review_status='submitted'); this
# in-process event only wakes a waiting module sooner. A restart loses the event
# and nothing else: the wait endpoint re-reads PostgreSQL.
_review_events: Dict[str, asyncio.Event] = {}
_review_futures: Dict[str, asyncio.Future] = {}
REVIEW_RECHECK_SECONDS = float(os.environ.get("REVIEW_RECHECK_SECONDS", "5"))


def _domain_http_error(exc: "population_review.DomainError") -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


# ----------------------------------------------------------------------
# Population cohort endpoints (Interactive Subpages)
# ----------------------------------------------------------------------

@app.post("/populations/set-labelling-mode")
async def set_labelling_mode(
    session_id: str = Query(..., description="The ID of the current session"),
    mode: str = Query(..., description="Labelling mode: 'manual' or 'automatic'")
):
    if mode not in ["manual", "automatic"]:
        raise HTTPException(status_code=400, detail="mode must be 'manual' or 'automatic'")
    try:
        await population_review.ensure_cohort(session_id, criteria={"labelling_mode": mode})
    except population_review.DomainError as exc:
        raise _domain_http_error(exc)
    return {"status": "success", "mode": mode}


@app.get("/populations/cohort-state")
async def get_cohort_state(
    session_id: str = Query(..., description="The ID of the current session"),
):
    """The Population subpages' state, assembled from the cohort rows."""
    try:
        cohort = await population_review.get_cohort(session_id)
    except population_review.DomainError as exc:
        raise _domain_http_error(exc)
    return population_review.cohort_state(cohort)


@app.post("/populations/update-cohort-list")
async def update_cohort_list(data: dict):
    """Replace the draft cohort membership from the Data Browser subpage."""
    session_id = data.get("session_id")
    new_datasetlist = [str(uid) for uid in data.get("datasetlist", []) or []]
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    try:
        cohort = await population_review.ensure_cohort(session_id)
        cohort = await population_review._api(
            "PUT",
            f"/cohorts/{cohort['cohort_id']}/members",
            json={"volume_series_instance_uids": new_datasetlist},
        )
    except population_review.DomainError as exc:
        raise _domain_http_error(exc)

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
    return {"status": "success", "count": len(cohort.get("members") or [])}


@app.post("/populations/save-labels")
async def save_labels(data: dict):
    """Record the doctor's labels from the Patient Labeling subpage."""
    session_id = data.get("session_id")
    labels_list = data.get("labels", []) or []
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    try:
        cohort = await population_review.get_cohort(session_id)
        if cohort is None:
            raise HTTPException(status_code=409, detail="The population cohort has not been defined yet")
        writes = population_review.manual_label_writes(cohort, labels_list)
        if writes:
            await population_review._api(
                "PATCH", f"/cohorts/{cohort['cohort_id']}/labels", json={"labels": writes}
            )
    except population_review.DomainError as exc:
        raise _domain_http_error(exc)
    return {"status": "success", "saved_count": len(writes)}


@app.post("/populations/approve-cohort")
async def approve_cohort(data: dict):
    """The doctor's approval — freezes the cohort (one-way)."""
    session_id = data.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    try:
        cohort = await population_review.get_cohort(session_id)
        if cohort is None:
            raise HTTPException(
                status_code=400,
                detail="Cannot approve: no volumes in the cohort. The population agent must discover volumes first.",
            )
        result = await population_review._api("POST", f"/cohorts/{cohort['cohort_id']}/freeze")
    except population_review.DomainError as exc:
        raise _domain_http_error(exc)

    event = result.get("event")
    if event:
        # Broadcast the event PostgreSQL already committed, with its cursor.
        await ws_manager.send_ws_message(
            event["msg_type"],
            event["payload"],
            session_id=session_id,
            event_id=event["event_id"],
        )
    logger.info(
        "[CohortApproval] Session %s: approved %s labeled volumes out of %s total.",
        session_id,
        result["labeled_count"],
        result["total_count"],
    )
    return {
        "status": "success",
        "labeled_count": result["labeled_count"],
        "cohort_id": cohort["cohort_id"],
    }


@app.post("/populations/upload-targets")
async def upload_targets(
    session_id: str = Query(..., description="The ID of the current session"),
    file: UploadFile = File(...)
):
    """Labels from a doctor-uploaded targets file (read, never stored)."""
    try:
        cohort = await population_review.get_cohort(session_id)
        if cohort is None:
            raise HTTPException(status_code=409, detail="The population cohort has not been defined yet")
        writes, unmatched = population_review.uploaded_target_writes(cohort, await file.read())
        if writes:
            await population_review._api(
                "PATCH", f"/cohorts/{cohort['cohort_id']}/labels", json={"labels": writes}
            )
    except population_review.DomainError as exc:
        raise _domain_http_error(exc)

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
        "message": "Targets recorded on the cohort",
        "session_id": session_id,
        "labeled_count": sum(1 for item in writes if item["has_target_disease"] is not None),
        "unmatched_ids": unmatched[:100],
    }


# ----------------------------------------------------------------------
# Segmentation review
# ----------------------------------------------------------------------

async def _review_batch_or_404(session_id: str) -> dict:
    batch = await population_review.current_batch(session_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Segmentation review is not available")
    return batch


@app.post("/segmentation-review/draft")
async def segmentation_review_draft(data: dict):
    """Commit UI review choices durably without resolving the agent's wait."""
    session_id = str(data.get("session_id") or "")
    decisions = data.get("decisions")
    if not session_id or "/" in session_id or "\\" in session_id or ".." in session_id:
        raise HTTPException(status_code=400, detail="A valid session_id is required")
    if not isinstance(decisions, dict):
        raise HTTPException(status_code=400, detail="decisions must be an object")
    allowed_statuses = {"accepted", "rejected", "modified"}
    if any(str(status) not in allowed_statuses for status in decisions.values()):
        raise HTTPException(status_code=400, detail="Review contains an invalid status")
    try:
        batch = await _review_batch_or_404(session_id)
        request_run_id = str(data.get("run_id") or "")
        batch_run_id = str((batch.get("config") or {}).get("correlation_run_id") or "")
        if request_run_id and batch_run_id and request_run_id != batch_run_id:
            raise HTTPException(status_code=409, detail="Segmentation run has changed")
        await population_review._api(
            "PUT",
            f"/segmentation-runs/{batch['segmentation_run_id']}/review/draft",
            json={"decisions": {str(key): str(value) for key, value in decisions.items()}},
        )
    except population_review.DomainError as exc:
        raise _domain_http_error(exc)
    return {"status": "ok"}


@app.post("/segmentation-review")
async def segmentation_review(data: dict):
    """The one final review submission."""
    from agents.execution_agents.segmentation.progressive import artifact_key_for_pair as _key

    session_id = str(data.get("session_id") or "")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    accepted = [str(item) for item in data.get("accepted", []) or []]
    rejected = [str(item) for item in data.get("rejected", []) or []]
    modified = [str(item) for item in data.get("modified", []) or []]
    logger.info(
        "[Review] Received triage for session=%s: %s accepted, %s rejected, %s modified",
        session_id, len(accepted), len(rejected), len(modified),
    )

    import hashlib
    import json as _json

    request_hash = hashlib.sha256(
        _json.dumps(
            {
                "accepted": sorted(set(accepted)),
                "rejected": sorted(set(rejected)),
                "modified": sorted(set(modified)),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    try:
        batch = await _review_batch_or_404(session_id)
        run_id = batch["segmentation_run_id"]
        batch = await population_review._api(
            "POST", f"/segmentation-runs/{run_id}/review/begin", json={"request_hash": request_hash}
        )
    except population_review.DomainError as exc:
        raise _domain_http_error(exc)

    selected: dict[str, str] = {}
    if batch["review_status"] != "submitted" and modified:
        pairs = {pair["pair_key"]: pair for pair in batch.get("pairs") or []}
        volume_keys = {}
        for pair in pairs.values():
            volume_keys.setdefault(pair["volume_series_instance_uid"], []).append(pair["pair_key"])
        targets = {target["target_key"]: target for target in batch.get("targets") or []}
        service = SegmentationService()
        try:
            for raw_key in modified:
                key = raw_key if raw_key in pairs else (volume_keys.get(raw_key) or [None])[0]
                pair = pairs.get(key or "")
                if pair is None:
                    raise HTTPException(status_code=400, detail=f"Unknown segmentation pair {raw_key!r}")
                temp_mask = ws_sub(session_id, f"temp_segmentations/seg_{_key(pair['pair_key'])}_modified.nii.gz")
                if not os.path.isfile(temp_mask):
                    raise HTTPException(
                        status_code=409,
                        detail=f"Pair {key!r} is marked modified but no edited mask was uploaded",
                    )
                published = await service.publish_modified_mask(
                    volume_uid=pair["volume_series_instance_uid"],
                    mask_path=str(temp_mask),
                    target_label=(targets.get(pair["target_key"]) or {}).get("label") or pair["target_key"],
                    pair_key=pair["pair_key"],
                )
                selected[key] = published["segmentation_series_uid"]
        except Exception as exc:
            try:
                await population_review._api(
                    "POST", f"/segmentation-runs/{run_id}/review/abort", json={"request_hash": request_hash}
                )
            except population_review.DomainError:
                logger.exception("Could not return review %s to pending", run_id)
            if isinstance(exc, HTTPException):
                raise
            logger.exception("Publishing edited masks failed for session %s", session_id)
            raise HTTPException(
                status_code=502,
                detail=f"The edited mask could not be published to Orthanc: {exc}",
            )
        finally:
            await service.close()

    try:
        result = await population_review._api(
            "POST",
            f"/segmentation-runs/{run_id}/review/complete",
            json={"accepted": accepted, "rejected": rejected, "modified": modified, "selected": selected},
        )
    except population_review.DomainError as exc:
        raise _domain_http_error(exc)

    event = result.get("event")
    if event:
        await ws_manager.send_ws_message(
            event["msg_type"], event["payload"], session_id=session_id, event_id=event["event_id"]
        )
    waiter = _review_events.get(session_id)
    if waiter is not None:
        waiter.set()

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
    workspace_folder = str(workspace(session_id))
    output_dir = str(ws_sub(session_id, "temp_segmentations"))

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
    base_volume_path = str(ws_sub(session_id, f"volumes/{series_instance_uid}.nii.gz"))
    original_mask_path = str(ws_sub(session_id, f"masks/{artifact_key}.nii.gz"))
    legacy_pair_name_is_safe = "/" not in pair_id and "\\" not in pair_id
    if not os.path.exists(original_mask_path) and legacy_pair_name_is_safe:
        original_mask_path = str(ws_sub(session_id, f"masks/{pair_id}.nii.gz"))

    import nibabel as nib
    import numpy as np

    raw_bytes = await file.read()
    actual_bytes = len(raw_bytes)
    if os.path.exists(base_volume_path):
        ref_nii = nib.load(base_volume_path)
    elif os.path.exists(original_mask_path):
        ref_nii = nib.load(original_mask_path)
    else:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Neither the base volume nor the original mask exists for {pair_id}; "
                "there is no reference grid to place the edited mask on."
            ),
        )
    ref_shape = tuple(int(value) for value in ref_nii.shape[:3])
    expected_voxels = int(np.prod(ref_shape))
    dtype = {
        expected_voxels: np.uint8,
        expected_voxels * 2: np.uint16,
        expected_voxels * 4: np.float32,
    }.get(actual_bytes)
    if dtype is None:
        # An edited mask with a different grid is rejected, never re-shaped
        # into something that merely has the right number of bytes.
        raise HTTPException(
            status_code=422,
            detail=(
                f"Edited mask size mismatch: the reference grid {ref_shape} holds "
                f"{expected_voxels} voxels, but {actual_bytes} bytes were uploaded. "
                "The mask must be edited on the source volume's own grid."
            ),
        )
    try:
        reshaped_array = np.frombuffer(raw_bytes, dtype=dtype).reshape(ref_shape, order="F")
        nib.save(nib.Nifti1Image(reshaped_array, ref_nii.affine), file_path)
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
    # Catalogue the regenerated preview (a render is a cache entry).
    await register_render(
        session_id,
        render_logical_key(pair_id, artifact_key),
        gif_path,
        metadata={
            "schema_version": 1,
            "pair_id": pair_id,
            "volume_uid": series_instance_uid,
            "target_id": target_id or None,
            "source": "user_modified_mask",
        },
    )
    overlay["gifUrl"] = (
        f"{str(request.base_url).rstrip('/')}/files/"
        f"{os.path.basename(workspace_folder)}/{gif_path}"
    )
    overlay["updatedAt"] = os.stat(
        workspace_subpath(workspace_folder, gif_path)
    ).st_mtime_ns

    return {
        "message": "Modified mask saved successfully; it is published as a SEG when the review is submitted",
        "session_id": session_id,
        "status": "saved",
        "overlay": overlay,
    }


@app.get("/segmentation-review/mask/{session_id}/{series_uid}")
async def get_segmentation_mask(
    session_id: str,
    series_uid: str,
    target_id: str | None = Query(None),
):
    """Serve the agent-generated mask for a given session and series UID as an NRRD file. The mask is converted from NIfTI (.nii.gz) to NRRD on-the-fly so the existing frontend SegmentationReader.parseNrrdData pipeline works without modification."""
    import io
    import numpy as np

    try:
        import nibabel as nib
        import nrrd
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"Required library missing: {e}")

    workspace_folder = str(workspace(session_id))

    # Search locations in priority order
    pair_id = f"{series_uid}__{target_id}" if target_id else series_uid
    artifact_key = artifact_key_for_pair(pair_id)
    candidates = [
        str(ws_sub(session_id, f"temp_segmentations/seg_{artifact_key}_modified.nii.gz")),
        str(ws_sub(session_id, f"masks/{artifact_key}.nii.gz")),
    ]
    if "/" not in pair_id and "\\" not in pair_id:
        candidates.extend(
            [
                str(ws_sub(session_id, f"temp_segmentations/seg_{pair_id}_modified.nii.gz")),
                str(ws_sub(session_id, f"masks/{pair_id}.nii.gz")),
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
        volume_path = str(ws_sub(session_id, f"volumes/{series_uid}.nii.gz"))
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
    """Long-poll until this session's segmentation review is submitted."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1, timeout)
    waiter = _review_events.setdefault(session_id, asyncio.Event())
    while True:
        try:
            batch = await population_review.current_batch(session_id)
        except population_review.DomainError as exc:
            raise _domain_http_error(exc)
        if batch is not None and batch["review_status"] == "submitted":
            _review_events.pop(session_id, None)
            return population_review.review_payload(batch)
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise HTTPException(status_code=408, detail="Review timeout")
        try:
            await asyncio.wait_for(
                asyncio.shield(waiter.wait()), timeout=min(REVIEW_RECHECK_SECONDS, remaining)
            )
            waiter.clear()
        except asyncio.TimeoutError:
            pass

# ----------------------------------------------------------------------
# Session Recovery
# ----------------------------------------------------------------------


@app.post("/session/{session_id}/recover")
async def recover_session(session_id: str, restart: bool = False):
    """Re-create an in-memory Session from persisted state after a process restart."""
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
    from startup_logging import uvicorn_log_config

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8150,
        reload=True,
        log_config=uvicorn_log_config("Agents API"),
    )
