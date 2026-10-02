"""
LaTeX Template MCP Tool

Provides tools for reading LaTeX templates and their filling guides.
Templates are stored in templates/, next to this module.

Available templates:
  - ieee_research_paper  (full 6-10 page IEEE conference paper)
  - short_study          (concise 3-5 page study report)
  - one_page_abstract    (one-page structured abstract)
"""

import json
import os

from .._toolkit import tool
from ..common.manifests import READ_ONLY
from pydantic import BaseModel, ConfigDict, Field


# ── Constants ──────────────────────────────────────────────────────────────────

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


# ── Input Models ───────────────────────────────────────────────────────────────

class GetTemplateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_name: str = Field(
        ...,
        description=(
            "Name of the template to load (without extension). "
            "Available: 'ieee_research_paper', 'short_study', 'one_page_abstract'. "
            "Choose based on the selection_criteria in the guide."
        ),
        min_length=1,
        max_length=128,
    )


# ── Tools ──────────────────────────────────────────────────────────────────────

@tool(
    name="list_latex_templates",
    visibility={"agent"},
    idempotent=True,
    timeout_s=45,
    **READ_ONLY,
)
async def list_latex_templates() -> str:
    """List all available LaTeX templates with their descriptions and selection criteria.

    Call this first to decide which template to use based on the study results.
    The response includes:
    - Available template names and descriptions
    - Selection criteria (rules for choosing the right template)
    - Filling instructions

    Returns:
        str: JSON with template listing and selection guidance.
    """
    guide_path = os.path.join(TEMPLATES_DIR, "template_guide.json")
    try:
        if os.path.exists(guide_path):
            with open(guide_path, "r", encoding="utf-8") as f:
                guide = json.load(f)

            listing = {
                "status": "ok",
                "available_templates": [],
                "selection_criteria": guide.get("selection_criteria", {}),
                "filling_instructions": guide.get("filling_instructions", ""),
            }
            for name, info in guide.get("templates", {}).items():
                listing["available_templates"].append({
                    "name": name,
                    "description": info.get("description", ""),
                    "use_when": info.get("use_when", ""),
                    "placeholder_count": len(info.get("placeholders", {})),
                })
            return json.dumps(listing, indent=2)
        else:
            return json.dumps({
                "status": "ok",
                "available_templates": [
                    {"name": n} for n in _list_templates()
                ],
            }, indent=2)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Failed to list templates: {str(e)}",
        }, indent=2)


@tool(
    name="get_latex_template",
    visibility={"agent"},
    idempotent=True,
    timeout_s=45,
    **READ_ONLY,
)
async def get_latex_template(params: GetTemplateInput) -> str:
    """Load a specific LaTeX template and its per-placeholder filling guide.

    Returns the raw LaTeX template with <<PLACEHOLDER>> markers, plus the
    guide section for that template explaining what data to put in each
    placeholder and which agent outputs to source it from.

    Workflow:
    1. Call list_latex_templates to see options and selection criteria
    2. Call read_all_agent_outputs to assess study results
    3. Choose the template based on selection criteria
    4. Call this tool (get_latex_template) with the chosen template_name
    5. Replace each <<PLACEHOLDER>> with LaTeX content from agent outputs
    6. Call latex_to_pdf with the filled template

    Args:
        params (GetTemplateInput):
            - template_name: 'ieee_research_paper' | 'short_study' | 'one_page_abstract'

    Returns:
        str: JSON with 'template' (LaTeX source), 'guide' (placeholder instructions),
             and 'selection_criteria' for reference.
    """
    template_path = os.path.join(TEMPLATES_DIR, f"{params.template_name}.tex")
    guide_path = os.path.join(TEMPLATES_DIR, "template_guide.json")

    if not os.path.exists(template_path):
        return json.dumps({
            "status": "error",
            "message": f"Template '{params.template_name}' not found.",
            "available_templates": _list_templates(),
        }, indent=2)

    try:
        with open(template_path, "r", encoding="utf-8") as f:
            template_content = f.read()

        template_guide = {}
        selection_criteria = {}
        filling_instructions = ""
        if os.path.exists(guide_path):
            with open(guide_path, "r", encoding="utf-8") as f:
                full_guide = json.load(f)
            # Extract only the guide for the requested template
            template_guide = full_guide.get("templates", {}).get(params.template_name, {})
            selection_criteria = full_guide.get("selection_criteria", {})
            filling_instructions = full_guide.get("filling_instructions", "")

        return json.dumps({
            "status": "ok",
            "template_name": params.template_name,
            "template": template_content,
            "guide": template_guide,
            "selection_criteria": selection_criteria,
            "filling_instructions": filling_instructions,
        }, indent=2)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Failed to load template: {str(e)}",
        }, indent=2)


def _list_templates():
    """List available template names."""
    if not os.path.exists(TEMPLATES_DIR):
        return []
    return [
        f.replace(".tex", "")
        for f in os.listdir(TEMPLATES_DIR)
        if f.endswith(".tex")
    ]
