"""Event ingress from MCP tools and other internal producers.

OpenResty blocks /agents/events and /agents/ws-broadcast at public ingress,
which covers the public path but not calls from inside the docker-compose
network or a published 8150 port -- see WEBSOCKET_FIX_PLAN.md W12. Both
routes additionally require WS_INTERNAL_TOKEN (services/internal_auth.py).
"""

import logging

# The groq SDK's pydantic BaseModel subclass, not pydantic's. Kept exactly as
# app.py imported it so request validation is unchanged; switching to
# pydantic.BaseModel is a separate, behaviour-affecting change.
from groq import BaseModel as GroqBaseModel
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from socket_store import manager as ws_manager, send_ws_message
from models.tool_logging import ToolEvent
from tool_logging import receive_tool_event
from api.runtime import session_service
from services.internal_auth import require_internal_token

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_internal_token)])


class latex_output(GroqBaseModel):
    event_type: str
    content: dict
    session_id: str


class WsBroadcastRequest(BaseModel):
    type: str
    payload: dict = Field(default_factory=dict)
    session_id: str | None = None


@router.post("/events")
async def receive_event(event: ToolEvent | latex_output):
    logger.info(f"Received tool event: {event}")
    if isinstance(event, latex_output):
        await send_ws_message(
            event_type="latex_output",
            payload=event.content.get("frontend_data"),
            session_id=event.session_id,
        )
        return

    await receive_tool_event(event)


@router.post("/ws-broadcast")
async def ws_broadcast(body: WsBroadcastRequest):
    if body.type == "agent_waiting_for_user_input":
        agent_name = body.payload.get("agent")
        waiting_status = body.payload.get("status")
        if body.session_id and agent_name and waiting_status:
            await session_service.persist_agent_status(
                str(body.session_id),
                str(agent_name),
                str(waiting_status),
            )
    await ws_manager.send_ws_message(
        body.type, body.payload, session_id=body.session_id
    )
    return {"status": "ok"}
