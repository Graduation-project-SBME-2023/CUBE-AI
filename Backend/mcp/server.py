from tools import mcp
from fastapi import FastAPI
import uvicorn



if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
