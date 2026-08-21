"""
View Update Emitter

Provides a utility for execution agents to push structured data
to the frontend views via WebSocket. Each agent emits events that
map directly to a specific frontend view component.

WebSocket event types:
  - "population_output"    → DataCohortView
  - "segmentation_output"  → DataCohortView (segmentation results)
  - "features_output"      → FeaturesView
  - "modeling_output"      → ModelingView
  - "report_output"        → ReportingView (result reporter)
  - "latex_output"         → ReportingView (PDF generation)
"""

import json
import logging
from typing import Any, Dict

from socket_store import send_ws_message

logger = logging.getLogger(__name__)


async def emit_view_update(
    view_event: str,
    data: Dict[str, Any],
    session_id: str,
):
    """
    Send a structured update to a specific frontend view via WebSocket.

    Args:
        view_event: The WebSocket event type (e.g. "features_output").
        data: The payload dict to send to the frontend view.
        session_id: The session ID to scope this message to.
    """
    try:
        await send_ws_message(
            msg_type=view_event,
            payload=data,
            session_id=session_id,
        )
        logger.info(f"Emitted view update: {view_event} (session={session_id})")
    except Exception as e:
        logger.error(f"Failed to emit view update {view_event}: {e}")


async def emit_population_output(
    data: Dict[str, Any],
    session_id: str,
):
    """Emit population/cohort selection results to DataCohortView."""
    await emit_view_update("population_output", data, session_id)


async def emit_segmentation_output(
    data: Dict[str, Any],
    session_id: str,
):
    """Emit segmentation results to DataCohortView."""
    await emit_view_update("segmentation_output", data, session_id)


async def emit_features_output(
    data: Dict[str, Any],
    session_id: str,
):
    """Emit feature extraction results to FeaturesView."""
    await emit_view_update("features_output", data, session_id)


async def emit_modeling_output(
    data: Dict[str, Any],
    session_id: str,
):
    """Emit modeling/classification results to ModelingView."""
    await emit_view_update("modeling_output", data, session_id)


async def emit_report_output(
    data: Dict[str, Any],
    session_id: str,
):
    """Emit result reporter output to ReportingView."""
    await emit_view_update("report_output", data, session_id)


async def emit_latex_output(
    data: Dict[str, Any],
    session_id: str,
):
    """Emit LaTeX/PDF generation output to ReportingView."""
    await emit_view_update("latex_output", data, session_id)
