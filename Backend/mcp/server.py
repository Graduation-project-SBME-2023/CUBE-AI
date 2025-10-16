# server.py
from fastapi import FastAPI, HTTPException
from tools import registered_tools, app
from typing import Dict, Any

# API endpoints
@app.post("/api/run/{tool_name}")
async def run_tool(tool_name: str, input_data: Dict[str, Any]):
    """
    Run a registered tool by name with the provided input data.
    """
    if tool_name not in registered_tools:
        raise HTTPException(status_code=404, detail=f"Tool {tool_name} not found")
    
    tool_func = registered_tools[tool_name]
    try:
        # Call the tool function with the input data unpacked as kwargs
        result = await tool_func(**input_data)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Entry point
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
