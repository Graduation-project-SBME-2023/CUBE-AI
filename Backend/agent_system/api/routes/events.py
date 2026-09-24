"""Event ingress from MCP tools and other internal producers.

OpenResty blocks /agents/events and /agents/ws-broadcast at public ingress.
"""

import logging

# The groq SDK's pydantic BaseModel subclass, not pydantic's. Kept exactly as
# app.py imported it so request validation is unchanged; switching to
# pydantic.BaseModel is a separate, behaviour-affecting change.
from groq import BaseModel
from fastapi import APIRouter

from socket_store import manager as ws_manager, send_ws_message
from models.tool_logging import ToolEvent
from tool_logging import receive_tool_event
from api.runtime import session_service

logger = logging.getLogger(__name__)

router = APIRouter()


class latex_output(BaseModel):
    event_type: str
    content: dict
    session_id: str


@router.post("/events")
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


@router.post("/ws-broadcast")
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
