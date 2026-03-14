"""
Literature Context MCP Tool

Reads the literature review context saved by the planner during Phase 2.
The file `literature_context.json` in the workspace contains:
  - knowledge_base: Full domain knowledge built from PubMed, MeSH, etc.
  - sources_guide: Curated sources and references for citing
  - hypothesis: The confirmed PICO hypothesis
"""

import json
import os

from . import mcp


# ── Constants ──────────────────────────────────────────────────────────────────

WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "../../agent_system/workspace")


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="read_literature_context",
    annotations={
        "title": "Read Literature Review Context",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def read_literature_context() -> str:
    """Read the literature review context saved during the planning phase.

    This contains the full knowledge base built from PubMed, MeSH, Wikipedia,
    Semantic Scholar, and Europe PMC — plus a curated sources guide for citations.

    Use this data for:
    - Writing the Related Work / Literature Review section
    - Adding proper references and citations
    - Comparing study results against published benchmarks
    - Providing clinical context in the Introduction

    Returns:
        str: JSON with 'knowledge_base', 'sources_guide', and 'hypothesis' fields.
    """
    lit_path = os.path.join(WORKSPACE_DIR, "literature_context.json")
    
    if not os.path.exists(lit_path):
        return json.dumps({
            "status": "not_available",
            "message": "No literature context found. The planner may not have run the literature review phase.",
        }, indent=2)
    
    try:
        with open(lit_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        return json.dumps({
            "status": "ok",
            "knowledge_base": data.get("knowledge_base", ""),
            "sources_guide": data.get("sources_guide", ""),
            "hypothesis": data.get("hypothesis", {}),
        }, indent=2)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Failed to read literature context: {str(e)}",
        }, indent=2)
