import os
import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
AGENT_SYSTEM_ROOT = BACKEND_ROOT / "agent_system"
for import_root in (BACKEND_ROOT, AGENT_SYSTEM_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

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

def main():
    """Send one manual PDF-output WebSocket event to a running backend."""
    asyncio.run(send_pdf())


if __name__ == "__main__":
    main()
