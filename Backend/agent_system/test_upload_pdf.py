import os
import asyncio
from agents.execution_agents.segmentation.ws_client_sender import send_ws_message_remote

# workspace_dir = os.path.join(os.path.dirname(__file__), "workspace")
# pdf_path = os.path.join(workspace_dir, "document.pdf")

frontend_data = {
   "latex_pdf": "http://localhost:8150/static/document.pdf"
}


async def send_pdf(session_id=None):
    await send_ws_message_remote(
        msg_type="latex_output",
        payload=frontend_data,
        session_id=session_id
    )

# Run inside asyncio context
asyncio.run(send_pdf())