import os
from storage_paths import workspace_subpath
from datetime import datetime
from typing import Any, Dict, Optional, TYPE_CHECKING
import aiofiles
import json
import logging

from clients.backend_client import BackendClient
from models.execution import AgentState, AgentStatus  # noqa: F401 (kept for the segmentation publisher)
from services.run_context import agent_type_of
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

CRITICAL_PIPELINE_TOOLS = {
    ("segmentation_agent", "segmentation_tool"),
    # FunctionTool exposes the Python wrapper's name at runtime.  Keep the
    # legacy name above for persisted/older events, but stop the pipeline for
    # the name emitted by the current segmentation agent as well.
    ("segmentation_agent", "run_segmentation_with_guard"),
}


def is_fatal_tool_failure(source: str, tool_name: str, failed: bool) -> bool:
    return failed and (source, tool_name) in CRITICAL_PIPELINE_TOOLS


def raise_for_fatal_tool_failure(
    source: str,
    tool_name: str,
    failed: bool,
    content: str,
) -> None:
    if is_fatal_tool_failure(source, tool_name, failed):
        raise RuntimeError(
            f"{source}.{tool_name} failed: {str(content)[:500]}"
        )

# ── Agent-to-view mapping for tool result emission ────────────────────────────

def _parse_tool_content(content: Any) -> Dict[str, Any]:
    """Attempt to parse tool result content as JSON, always returning a dict."""
    if not content:
        return {}

    # Some MCP/AutoGen versions hand us an already-decoded result instead of
    # the JSON string representation used by older versions.
    if isinstance(content, dict):
        return content

    try:
        parsed = json.loads(content) if isinstance(content, str) else content

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


async def _read_agent_output(session_id: str, agent_type: str) -> dict:
    """Read an agent's structured output from its run."""
    if not session_id or not agent_type:
        return {}
    for run in await BackendClient.get_agent_runs(session_id):
        step = run.get("step") or {}
        if step.get("agent_type") != agent_type:
            continue
        agent_output = (run.get("output_summary") or {}).get("agent_output")
        if not agent_output:
            continue
        outputs = agent_output.get("outputs") or {}
        return {
            "agent_id": agent_type,
            "agent_run_id": run.get("agent_run_id"),
            "step_id": run.get("step_id"),
            "step_order": step.get("step_order"),
            "agent_role": agent_output.get("agent_role"),
            "status": agent_output.get("status"),
            "timestamp": agent_output.get("recorded_at"),
            "inputs": agent_output.get("inputs") or {},
            "outputs": outputs,
            "errors": agent_output.get("errors") or [],
            "warnings": [
                item.get("message")
                for item in (run.get("warnings") or {}).get("items", [])
            ],
        }
    return {}


async def _emit_tool_result_to_view(
        source: str, tool_name: str, content: str,
        session_id: str, workspace_folder: str
):
    # `source` is the graph NODE name, which carries the step. Views and
    # the UI speak in agent types, so translate once, here.
    source = agent_type_of(source) or source
    parsed = _parse_tool_content(content)
    data, msg_type = None, None

    # ── Population Planner agent ──────────────────────────────────────────
    if "population" in source.lower() or "study_population" in source.lower():
        msg_type = "population_output"
        if tool_name == "write_agent_output":
            agent_data = await _read_agent_output(session_id, "study_population_planner_agent")
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
            agent_data = await _read_agent_output(session_id, "segmentation_agent")
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
            agent_data = await _read_agent_output(session_id, "feature_extraction_agent")
            payload = agent_data if agent_data else parsed
            data = payload
            await emit_features_output(payload, session_id)

        elif tool_name == "radiomics_extract_features":
            # No chart_data from extraction — counts, and the Application API
            # exports the rows are downloadable from.
            logger.info(f"Content: {content}, parsed: {parsed}")
            data = {
                "tool": "radiomic_extraction",
                "status": parsed.get("status", "success"),
                "agent_run_id": parsed.get("agent_run_id"),
                "radiomics_rows": parsed.get("radiomics_rows"),
                "exports": parsed.get("exports", {}),
                "num_subjects": parsed.get("num_subjects"),
                "num_features": parsed.get("num_features", {}),
                "mask_labels": parsed.get("mask_labels", []),
                "unlabeled_subject_ids": parsed.get("unlabeled_subject_ids", []),
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
                # The frozen sets the modeling agent will read.
                "feature_sets": parsed.get("feature_sets", []),
                "exports": parsed.get("exports", {}),
                "label_supervised_on_full_data": parsed.get("label_supervised_on_full_data"),
                "task_type": parsed.get("task_type"),
                # NEW — full chart payload for React components
                "chart_data": parsed.get("chart_data"),
            }
            await emit_features_output(data, session_id)

    # ── Modeling agent ────────────────────────────────────────────────────
    elif "modeling" in source.lower():
        if tool_name == "write_agent_output":
            # This summary is for the reporter agent. It has no chart_data,
            # so it must never be persisted or replayed as a modeling view
            # event; otherwise its fields (inputs, outputs, warnings, etc.)
            # appear as fake experiments in the UI.
            return None, None

        elif tool_name == "pycaret_train_classifier":
            msg_type = "modeling_output"
            # Build a per-experiment entry with chart_data for the frontend.
            # The frontend ModelingView expects Record<string, ModelingData>
            # keyed by experiment_name so each call appends a new accordion.
            exp_name = parsed.get("experiment_name") or "experiment"
            experiment_entry = {
                "tool": "classification",
                "status": parsed.get("status", "completed"),
                "experiment_name": exp_name,
                # current this flow: the comparison is rows; predictions are artifacts.
                "comparison_key": parsed.get("comparison_key"),
                "feature_set_id": parsed.get("feature_set_id"),
                "selected_variant": parsed.get("selected_variant"),
                "model_results": parsed.get("model_results", []),
                "final_model_path": parsed.get("final_model_path"),
                "individual_model_paths": parsed.get("individual_model_paths", {}),
                "model_test_predictions": parsed.get("model_test_predictions", {}),
                "exports": parsed.get("exports", {}),
                "selection_holdout_used": parsed.get("selection_holdout_used"),
                "generalisation_estimate": parsed.get("generalisation_estimate"),
                # Full chart payload for React chart components
                "chart_data": parsed.get("chart_data"),
            }
            data = {exp_name: experiment_entry}
            await emit_modeling_output(data, session_id)

    # ── Result Reporter agent ─────────────────────────────────────────────
    elif "result_reporter" in source.lower():
        msg_type = "report_output"
        if tool_name == "write_agent_output":
            agent_data = await _read_agent_output(session_id, "result_reporter_agent")
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
            agent_data = await _read_agent_output(session_id, "latex_reporter_agent")
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
    """The live view of a run, on top of the durable one."""

    def __init__(
        self,
        session_service: "SessionService",
        session: "Session",
        runs: Optional[list] = None,
    ):
        self.session_service = session_service
        self.session = session
        # Keyed by NODE name, so two steps sharing an agent type are two
        # entries rather than one that overwrites the other.
        self.runs = {run.node_name: run for run in (runs or [])}
        self._last_active: Optional[str] = None

    @property
    def current_agent(self) -> Optional[str]:
        return agent_type_of(self._last_active) if self._last_active else None

    async def on_agent_active(self, source: str) -> None:
        """Called when a node does something."""
        if source not in self.runs:
            return
        if self._last_active == source:
            return
        self._last_active = source
        await self.session_service.update_current_agent(
            self.session, self.runs[source]
        )

    async def on_tool_result(
        self,
        source: str,
        tool_name: str,
        content: str,
        workspace_folder,
        *,
        execution_error: bool = False,
    ) -> bool:
        """Inspect a tool result and return whether it represents a failure."""
        if source not in self.runs:
            return bool(execution_error)

        # Only flag as error if the JSON status field is actually "error" or
        # "failed", not just because those words appear in a large payload.
        is_error = bool(execution_error)
        try:
            parsed_check = json.loads(content) if content else {}
            if isinstance(parsed_check, list) and parsed_check:
                inner = parsed_check[0].get("text", "")
                try:
                    parsed_check = json.loads(inner)
                except Exception:
                    pass
            if isinstance(parsed_check, dict):
                status_val = str(parsed_check.get("status", "")).lower()
                is_error = is_error or status_val in ("error", "failed")
        except Exception:
            pass

        msg_type, data = await _emit_tool_result_to_view(
            source, tool_name, content,
            self.session.session_id, workspace_folder
        )

        if not is_error:
            await self.on_agent_active(source)
        if msg_type is not None and data is not None:
            await self.session_service.update_tool_views(
                self.session, source, msg_type, data
            )
        return is_error

    async def on_pipeline_complete(self):
        """The graph finished. Every run closed itself as it ended."""
        self._last_active = None

    async def on_pipeline_error(self, error: Exception):
        """A failure outside any node (graph construction, cancellation)."""
        await self.session_service.report_pipeline_error(self.session, error)

    async def on_tool_event(self, event: ToolEvent) -> None:
        payload = event.model_dump(mode="json")
        log = {
            "type": "tool_update",
            "payload": payload,
            "session_id": event.session_id,
        }
        await self.session_service.update_agent_tool_logs(
            self.session, event.source, log
        )
