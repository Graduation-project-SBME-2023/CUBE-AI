from fastapi import FastAPI
import uvicorn

from tools import mcp  # Your FastMCP instance
# from tools import *  # modules decorated with @mcp.tool() now attach to the right instance
from typing import Optional, Dict, Any



api = FastAPI(title="MCP Server API", version="1.0.0")



# # Register all tools on startup
# register_tools_from_directory()


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
        print(f"✓ Found {tools_dict} tools")
    except Exception as e:
        return {"error": str(e), "tools": []}

    return {"tools": tools_list, "count": len(tools_list)}



    # @mcp.tool()
    # async def characterize_tumor(
    #     segmented_volume: str | None = None,
    #     metadata: dict | None = None,
    #     output_format: str = "json",
    #     include_visualization: bool = True,
    #     include_summary: bool = True,
    #     ctx: Dict | None = None,
    # ) -> str:
    #     """
    #     Characterize a segmented tumor using multiple complementary analysis techniques beyond radiomics.

    #     Args:
    #         segmented_volume: Path or identifier of the segmented 3D tumor volume.
    #         metadata: Optional dictionary of patient or imaging metadata.
    #         output_format: Format of the output report ('json', 'markdown', 'html').
    #         include_visualization: Whether to include visualization hooks or previews.
    #         include_summary: Whether to include summary statistics in the report.
    #         ctx: Optional MCP context (provided automatically when invoked as a tool).

    #     Returns:
    #         A structured tumor characterization report in the specified format.
    #     """
    #     return await _characterize_tumor_impl(
    #         segmented_volume,
    #         metadata,
    #         output_format,
    #         include_visualization,
    #         include_summary,
    #         ctx,
    #     )





# ----------------------
# Mount MCP ASGI app
# ----------------------
api.mount("/mcp", mcp.sse_app())




# ----------------------
# Run FastAPI + MCP
# ----------------------
if __name__ == "__main__":
    tool_count = len(getattr(mcp, "_tools", {}))
    print(f"\n🚀 Starting server with {tool_count} tools loaded")
    print(f"📍 FastAPI endpoints: http://localhost:8000")
    print(f"📍 API Docs: http://localhost:8000/docs")
    print(f"📍 Tools List: http://localhost:8000/tools\n")


    # Run FastAPI with uvicorn in hot-reload mode
    uvicorn.run(
        "app:api",           # path to your FastAPI instance
        host="0.0.0.0",
        port=8000,
        reload=True,         # Enable hot reload
        workers=1            # Recommended for reload mode
    )

