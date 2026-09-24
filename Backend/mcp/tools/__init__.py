"""MCP tool modules and the shared FastMCP instance they register on.

Each tool module decorates its functions with ``@mcp.tool()``; ``server.py``
imports the modules explicitly, so importing this package alone registers
nothing.
"""

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("My MCP Server")
