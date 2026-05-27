import os
from datetime import datetime
from typing import Optional, TYPE_CHECKING, Dict, Any
import aiofiles
import json
import logging

from models.execution import AgentState, AgentStatus
from models.tool_logging import ToolEvent

from view_emitter import (
    emit_population_output,
    emit_segmentation_output,
    emit_features_output,
    emit_modeling_output,
    emit_report_output,
    emit_latex_output,
)

if TYPE_CHECKING:
    from models.session import Session
    from services.session_service import SessionService

logger = logging.getLogger(__name__)

# ── Agent-to-view mapping for tool result emission ────────────────────────────

def _parse_tool_content(content: str) -> Dict[str, Any]:
    """Attempt to parse tool result content as JSON, always returning a dict."""
    if not content:
        return {}

    try:
        parsed = json.loads(content)

        # Case 1: already a dict
        if isinstance(parsed, dict):
            return parsed

        # Case 2: MCP-style content blocks
        if (
            isinstance(parsed, list)
            and len(parsed) > 0
            and isinstance(parsed[0], dict)
            and "text" in parsed[0]
        ):
            text_content = parsed[0]["text"]

            try:
                nested = json.loads(text_content)

                if isinstance(nested, dict):
                    return nested

                return {"data": nested}

            except json.JSONDecodeError:
                return {"raw": text_content[:2000]}

        # Fallback for lists or other structures
        return {"data": parsed}

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
        session_id: str, workspace_folder: str
):
    parsed = _parse_tool_content(content)
    data, msg_type = None, None

    # ── Population Planner agent ──────────────────────────────────────────
    if "population" in source.lower() or "study_population" in source.lower():
        msg_type = "population_output"
        if tool_name == "write_agent_output":
            agent_data = await _read_agent_output_file(workspace_folder, "study_population_planner_agent")
            payload = agent_data if agent_data else parsed
            data = payload
            await emit_population_output(payload, session_id)
        elif tool_name in ("generate_targets", "get_patient", "get_volume"):
            print(type(parsed))
            data = {
                "tool": tool_name,
                "status": parsed.get("status", "completed"),
                "data": parsed,
            }
            await emit_population_output(data, session_id)

    # ── Segmentation agent ────────────────────────────────────────────────
    elif "segmentation" in source.lower():
        msg_type = "segmentation_output"
        if tool_name == "write_agent_output":
            agent_data = await _read_agent_output_file(workspace_folder, "segmentation_agent")
            payload = agent_data if agent_data else parsed
            data = payload
            await emit_segmentation_output(payload, session_id)
        elif tool_name == "test_module":
            data = {
                "tool": tool_name,
                "status": parsed.get("status", "completed"),
                "data": parsed,
            }
            await emit_segmentation_output(data, session_id)

    # ── Feature Extraction agent ──────────────────────────────────────────
    elif "feature_extraction" in source.lower():
        msg_type = "features_output"
        if tool_name == "write_agent_output":
            agent_data = await _read_agent_output_file(workspace_folder, "feature_extraction_agent")
            payload = agent_data if agent_data else parsed
            data = payload
            await emit_features_output(payload, session_id)

        elif tool_name == "radiomics_extract_features":
            # No chart_data from extraction — just counts and paths
            logger.info(f"Content: {content}, parsed: {parsed}")
            data = {
                "tool": "radiomic_extraction",
                "status": parsed.get("status", "success"),
                "csv_paths": parsed.get("csv_paths", {}),
                "num_subjects": parsed.get("num_subjects"),
                "num_features": parsed.get("num_features", {}),
                "mask_labels": parsed.get("mask_labels", []),
                "processing_time_seconds": parsed.get("processing_time_seconds"),
            }
            await emit_features_output(data, session_id)

        elif tool_name == "radiomics_analyze_importance":
            # Forward chart_data directly — this is what drives the frontend charts
            logger.info(f"Content: {content}, parsed: {parsed}")
            data = {
                "tool": "feature_importance",
                "status": parsed.get("status", "success"),
                "method": parsed.get("method"),
                "feature_count": parsed.get("feature_count"),
                "result_files": parsed.get("result_files", {}),
                "target_column": parsed.get("target_column"),
                "task_type": parsed.get("task_type"),
                # NEW — full chart payload for React components
                "chart_data": parsed.get("chart_data"),
            }
            await emit_features_output(data, session_id)

    # ── Modeling agent ────────────────────────────────────────────────────
    elif "modeling" in source.lower():
        msg_type = "modeling_output"
        if tool_name == "write_agent_output":
            agent_data = await _read_agent_output_file(workspace_folder, "modeling_agent")
            payload = agent_data if agent_data else parsed
            data = payload
            await emit_modeling_output(payload, session_id)

        elif tool_name == "pycaret_train_classifier":
            # Forward chart_data directly — drives all frontend chart components
            data = {
                "tool": "classification",
                "status": parsed.get("status", "completed"),
                "experiment_name": parsed.get("experiment_name"),
                "model_summary": parsed.get("model_summary"),
                "final_model_path": parsed.get("final_model_path"),
                "created_files": parsed.get("created_files", []),
                "individual_model_paths": parsed.get("individual_model_paths", {}),
                "model_test_predictions": parsed.get("model_test_predictions", {}),
                # NEW — full chart payload for React components
                "chart_data": parsed.get("chart_data"),
            }
            await emit_modeling_output(data, session_id)

    # ── Result Reporter agent ─────────────────────────────────────────────
    elif "result_reporter" in source.lower():
        msg_type = "report_output"
        if tool_name == "write_agent_output":
            agent_data = await _read_agent_output_file(workspace_folder, "result_reporter_agent")
            payload = agent_data if agent_data else parsed
            data = payload
            await emit_report_output(payload, session_id)
        elif tool_name == "get_modeling_results":
            data = {
                "tool": tool_name,
                "status": "completed",
                "data": parsed,
            }
            await emit_report_output(data, session_id)

    # ── LaTeX Reporter agent ──────────────────────────────────────────────
    elif "latex" in source.lower() or "reporter" in source.lower():
        msg_type = "latex_output"
        if tool_name == "write_agent_output":
            agent_data = await _read_agent_output_file(workspace_folder, "latex_reporter_agent")
            payload = agent_data if agent_data else parsed
            data = payload
            await emit_latex_output(payload, session_id)
        elif tool_name == "latex_to_pdf":
            data = {
                "tool": "latex_to_pdf",
                "status": parsed.get("status", "completed"),
                "data": parsed,
            }
            await emit_latex_output(data, session_id)

    return msg_type, data


class PipelineMonitor:
    AGENT_ORDER = [
        "study_population_planner_agent",
        "segmentation_agent",
        "feature_extraction_agent",
        "modeling_agent",
        "result_reporter_agent",
        "latex_reporter_agent",
    ]

    def __init__(self, session_service: "SessionService", session: "Session"):
        self.session_service = session_service
        self.session = session
        self.agents: dict[str, AgentState] = {
            name: AgentState(name=name) for name in self.AGENT_ORDER
        }

        self._last_active: Optional[str] = None

    async def on_agent_active(self, source: str) -> None:
        """Call when an agent start doing something"""
        if source not in self.agents:
            return

        agent = self.agents[source]

        if self._last_active and self._last_active != source:
            await self._mark_completed(self._last_active)

        if agent.status in (AgentStatus.PENDING, AgentStatus.FAILED):
            agent.status = AgentStatus.RUNNING
            agent.started_at = datetime.now()
            self._last_active = source
            await self._broadcast()

    async def on_tool_result(self, source: str, tool_name: str, content: str, workspace_folder) -> None:
        """Inspect tool result for error signals, and broadcast logs if necessary."""
        if source not in self.agents:
            return

        lowered = content.lower()
        is_error = any(kw in lowered for kw in ["error", "exception", "failed", "traceback"])

        if is_error:
            agent = self.agents[source]
            agent.status = AgentStatus.FAILED
            agent.finished_at = datetime.now()
            agent.error = content[:300]
            self._last_active = source
            await self._broadcast()

        msg_type, data = await _emit_tool_result_to_view(
            source, tool_name, content,
            self.session.session_id, workspace_folder
        )

        await self.on_agent_active(source)
        await self.session_service.update_tool_views(self.session, source, msg_type, data)

    async def on_pipeline_complete(self):
        """Mark the last running agent and pipeline as done."""
        if self._last_active:
            await self._mark_completed(self._last_active)
        await self._broadcast()

    async def on_pipeline_error(self, error: Exception):
        """Mark whatever was running as failed."""
        if self._last_active and self.agents[self._last_active].status == AgentStatus.RUNNING:
            agent = self.agents[self._last_active]
            agent.status = AgentStatus.FAILED
            agent.finished_at = datetime.now()
            agent.error = str(error)
        await self._broadcast()

    async def _mark_completed(self, name: str) -> None:
        agent = self.agents[name]
        if agent.status == AgentStatus.RUNNING:
            agent.status = AgentStatus.COMPLETED
            agent.finished_at = datetime.now()

    async def on_tool_event(self, event: ToolEvent) -> None:
        agent = event.source
        msg_type = "tool_update"
        payload = event.model_dump(mode="json")
        session_id = event.session_id

        log = {
            "type": msg_type,
            "payload": payload,
            "session_id": session_id,
        }
        await self.session_service.update_agent_tool_logs(self.session, agent, log)
        # send to both front and db via session_service

    async def _broadcast(self):
        agents = [
            {
                "name": a.name,
                "status": a.status.value,
                "started_at": a.started_at.isoformat() if a.started_at else None,
                "finished_at": a.finished_at.isoformat() if a.finished_at else None,
                "error": a.error,
            }
            for a in self.agents.values()
        ]
        await self.session_service.handle_pipeline_update(self.session, agents)
