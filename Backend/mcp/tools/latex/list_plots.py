"""List this session's figure artifacts for inclusion in a LaTeX report.

Every plot the pipeline produces (feature-importance bars, ROC curves,
confusion matrices, ...) is published as a `figure_png`/`figure_jpeg`
artifact — bytes in PostgreSQL (A13) — by the agent that rendered it
(`radiomics_analyze_importance`, the modeling tools' `publish_figures`).
Nothing is left as a loose file under the workspace, so a tool that globs
`WORKSPACE_DIR` for `*.png` always finds zero: this queries the artifact
catalogue instead, which is the only place a figure actually lives.
"""

import json

from .. import mcp
from ..helpers import domain_api

_FIGURE_KINDS = ("figure_png", "figure_jpeg")


@mcp.tool()
async def list_report_figures() -> str:
    """List this session's figure artifacts available to embed in the report.

    Returns a JSON object with:
      - figures: list of objects with 'artifact_id', 'display_filename',
        'logical_key' and 'producer' (the agent run that created it)
      - count: total number of figures found

    An artifact_id here is not usable directly in `\\includegraphics`: first
    call `register_report_image(report_id, artifact_id, image_key)` for each
    figure you want in the manuscript, then reference that same `image_key`
    in the LaTeX source and pass it in `latex_to_pdf`'s `image_keys`.
    """
    try:
        figures: list[dict] = []
        for kind in _FIGURE_KINDS:
            figures.extend(await domain_api.list_session_artifacts(kind=kind))
    except domain_api.DomainApiError as exc:
        return json.dumps({"status": "error", "error_message": str(exc)}, indent=2)

    figures.sort(key=lambda item: item.get("created_at", ""))
    return json.dumps(
        {
            "figures": [
                {
                    "artifact_id": item["artifact_id"],
                    "display_filename": item["display_filename"],
                    "logical_key": item["logical_key"],
                    "producer": item.get("created_by_agent_run_id"),
                }
                for item in figures
            ],
            "count": len(figures),
        },
        indent=2,
    )
