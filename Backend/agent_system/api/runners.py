"""Planner and execution-pipeline callables that SessionService runs per session.

This module is the only place the HTTP layer touches AutoGen's GraphFlow.
"""

import asyncio
import logging
import os

from autogen_agentchat.base import TaskResult
from autogen_agentchat.messages import (
    ToolCallRequestEvent,
    ToolCallExecutionEvent,
    MemoryQueryEvent,
    UserInputRequestedEvent,
    ThoughtEvent,
    BaseChatMessage,
    BaseAgentEvent,
)
from autogen_agentchat.teams import GraphFlow

from storage_paths import workspace
from socket_store import manager as ws_manager
from agents.planning_agents.planner import ClinicalPlanner
from client_config import create_model_client_from_config
from models.session import Session
from services.session_service import load_runs
from services.run_context import agent_type_of
from services.pipeline_monitor import PipelineMonitor, raise_for_fatal_tool_failure
from services.population_handoff import require_population_handoff
from services.mask_store import MaskStore
from clients.backend_client import BackendClient
from teams.execution_team import BasicPipelineBuilder
from api.runtime import session_service

logger = logging.getLogger(__name__)

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
        # Cached masks are copies of what Orthanc already holds permanently, so
        # the run is the right place to drop them. Nothing the study depends on
        # lives under cache/.
        try:
            async with MaskStore() as store:
                await store.clear_session(session.session_id)
        except Exception:  # noqa: BLE001 - the TTL sweeper is the safety net
            logger.exception(
                "Could not clear the mask cache for session %s", session.session_id
            )
