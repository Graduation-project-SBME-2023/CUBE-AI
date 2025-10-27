from mcp.server.fastmcp import FastMCP
import importlib
import pkgutil
from pathlib import Path


mcp = FastMCP("My MCP Server")


def register_tools_from_directory(tools_dir: str = "tools"):
    """
    Discover and import all modules in the tools directory.
    Each module should have functions decorated with @mcp.tool().
    """
    tools_path = Path(tools_dir)
    
    if not tools_path.exists():
        print(f"✗ Tools directory '{tools_dir}' not found")
        return
    
    for module_info in pkgutil.iter_modules([str(tools_path)]):
        module_name = f"{tools_dir}.{module_info.name}"
        try:
            importlib.import_module(module_name)  # triggers @mcp.tool()
            print(f"✓ Loaded tools from: {module_name}")
        except Exception as e:
            print(f"✗ Error loading {module_name}: {e}")

# register_tools_from_directory()