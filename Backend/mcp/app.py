import logging
logger = logging.getLogger(__name__)
from fastapi import FastAPI
import uvicorn

from tools import mcp  
from typing import Optional, Dict, Any

import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))




api = FastAPI(title="MCP Server API", version="1.0.0")























# ----------------------
# FastAPI Routes
# ----------------------
@api.get("/")
async def root():
    """Root endpoint with server info."""
    tool_count = len(getattr(mcp, "_tools", {}))
    return {
        "message": "MCP Server with FastAPI",
        "status": "running",
        "tools_loaded": tool_count
    }


@api.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}



@api.get("/tools")
async def list_tools():
    """List all registered MCP tools."""
    tools_list = []

    try:
        # Await the async list_tools() method
        tools_dict = await mcp.list_tools()  
        for tool in tools_dict:
            tools_list.append({
                "name": tool.name,
                "description": tool.description
            })
        logger.info(f"✓ Found {tools_dict} tools")
    except Exception as e:
        return {"error": str(e), "tools": []}

    return {"tools": tools_list, "count": len(tools_list)}





# ----------------------
# Mount MCP ASGI app
# ----------------------
api.mount("/mcp", mcp.sse_app())




# ----------------------
# Run FastAPI + MCP
# ----------------------
if __name__ == "__main__":
    tool_count = len(getattr(mcp, "_tools", {}))
    logger.info(f"\n Starting server with {tool_count} tools loaded")
    base_url = os.environ.get("MONAI_URL", "http://localhost:8000")
    logger.info(f" FastAPI endpoints: {base_url}")
    logger.info(f" API Docs: {base_url}/docs")
    logger.info(f" Tools List: {base_url}/tools\n")


    # Run FastAPI with uvicorn in hot-reload mode
    uvicorn.run(
        "app:api",           # path to your FastAPI instance
        host="0.0.0.0",
        port=8500,
        reload=True,         # Enable hot reload
        workers=1            # Recommended for reload mode
    )

