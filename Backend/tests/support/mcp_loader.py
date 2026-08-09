from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType


_PACKAGE_NAME = "_cubeai_test_mcp_tools"
_TOOLS_DIR = Path(__file__).resolve().parents[2] / "mcp" / "tools"


def _ensure_private_tools_package() -> None:
    """Load Backend/mcp/tools under a private, non-conflicting name."""
    if _PACKAGE_NAME in sys.modules:
        return

    spec = importlib.util.spec_from_file_location(
        _PACKAGE_NAME,
        _TOOLS_DIR / "__init__.py",
        submodule_search_locations=[str(_TOOLS_DIR)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not create the private MCP tools package")

    package = importlib.util.module_from_spec(spec)
    sys.modules[_PACKAGE_NAME] = package
    spec.loader.exec_module(package)


def load_mcp_tool(dotted_module: str) -> ModuleType:
    """Import an MCP tool module without shadowing the installed `mcp` SDK.

    Example: ``load_mcp_tool("feature_extraction.cache_utils")``.
    """
    _ensure_private_tools_package()
    return importlib.import_module(f"{_PACKAGE_NAME}.{dotted_module}")

