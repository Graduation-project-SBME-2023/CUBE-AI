"""The LaTeX template tools: the report templates and their filling guides.

Templates are ``templates/<name>.tex`` next to this module; the guide
(``templates/template_guide.json``) describes each one and says how to choose:

  - ieee_research_paper  (full 6-10 page IEEE conference paper)
  - short_study          (concise 3-5 page study report)
  - one_page_abstract    (one-page structured abstract)
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .._toolkit import error_result, tool
from ..common.manifests import READ_ONLY

TEMPLATES_DIR = str(Path(__file__).parent / "templates")
GUIDE_FILE = "template_guide.json"


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
        # A name, never a path: it is joined to the templates directory.
        pattern=r"^[A-Za-z0-9_-]+$",
    )


def _templates_dir() -> Path:
    return Path(TEMPLATES_DIR)


def _template_names() -> list[str]:
    directory = _templates_dir()
    return sorted(path.stem for path in directory.glob("*.tex")) if directory.is_dir() else []


def _guide() -> dict[str, Any] | None:
    """The filling guide, or ``None`` when there is none. Raises ``OSError`` /
    ``ValueError`` when it exists but cannot be read."""
    path = _templates_dir() / GUIDE_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@tool(
    name="list_latex_templates",
    visibility={"agent"},
    idempotent=True,
    timeout_s=45,
    **READ_ONLY,
)
async def list_latex_templates() -> str | dict:
    """List all available LaTeX templates with their descriptions and selection criteria.

    Call this first to decide which template to use based on the study results.
    The response includes the template names and descriptions, the selection
    criteria (rules for choosing the right template) and the filling instructions.
    """
    try:
        guide = _guide()
    except (OSError, ValueError) as exc:
        return error_result("unavailable", f"Failed to list templates: {exc}")
    if guide is None:
        return json.dumps(
            {"status": "ok", "available_templates": [{"name": name} for name in _template_names()]},
            indent=2,
        )
    return json.dumps({
        "status": "ok",
        "available_templates": [
            {
                "name": name,
                "description": info.get("description", ""),
                "use_when": info.get("use_when", ""),
                "placeholder_count": len(info.get("placeholders", {})),
            }
            for name, info in guide.get("templates", {}).items()
        ],
        "selection_criteria": guide.get("selection_criteria", {}),
        "filling_instructions": guide.get("filling_instructions", ""),
    }, indent=2)


@tool(
    name="get_latex_template",
    visibility={"agent"},
    idempotent=True,
    timeout_s=45,
    **READ_ONLY,
)
async def get_latex_template(params: GetTemplateInput) -> str | dict:
    """Load a specific LaTeX template and its per-placeholder filling guide.

    Returns the raw LaTeX template with <<PLACEHOLDER>> markers, plus the
    guide section for that template explaining what data to put in each
    placeholder and which recorded results to source it from.

    Workflow:
    1. Call list_latex_templates to see options and selection criteria
    2. Assess the study results from your Node inputs and get_modeling_results
    3. Choose the template based on selection criteria
    4. Call this tool (get_latex_template) with the chosen template_name
    5. Replace each <<PLACEHOLDER>> with LaTeX content from those results
    6. Call latex_to_pdf with the filled template
    """
    template_path = _templates_dir() / f"{params.template_name}.tex"
    if not template_path.is_file():
        return error_result(
            "not_found",
            f"Template '{params.template_name}' not found.",
            available_templates=_template_names(),
        )
    try:
        template = template_path.read_text(encoding="utf-8")
        guide = _guide() or {}
    except (OSError, ValueError) as exc:
        return error_result("unavailable", f"Failed to load template: {exc}")
    return json.dumps({
        "status": "ok",
        "template_name": params.template_name,
        "template": template,
        "guide": guide.get("templates", {}).get(params.template_name, {}),
        "selection_criteria": guide.get("selection_criteria", {}),
        "filling_instructions": guide.get("filling_instructions", ""),
    }, indent=2)
