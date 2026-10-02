"""Register a figure artifact as a named image source for a report.

`latex_to_pdf` only materializes images it was told about: the Application
API validates, in the same transaction as the compile, that every key in
`image_keys` names a `figure_png`/`figure_jpeg` artifact already registered
against this report (`POST /reports/{report_id}/images`). Nothing writes
that row automatically, so a figure discovered via `list_report_figures`
still cannot be `\\includegraphics`'d until this tool names it.
"""

import json
from typing import Annotated

from pydantic import Field

from .._toolkit import tool
from ..common.manifests import WRITES
from ..common import domain_api


@tool(
    name="register_report_image",
    visibility={"agent"},
    data_scopes={"db_write"},
    timeout_s=150,
    **WRITES,
)
async def register_report_image(
    report_id: Annotated[
        str, Field(description="The report_id returned by create_report.")
    ],
    artifact_id: Annotated[
        str,
        Field(description="A figure artifact_id from list_report_figures."),
    ],
    image_key: Annotated[
        str,
        Field(
            description=(
                "The name to reference this image by in the LaTeX source, e.g. "
                "'feature_importance.png'. Use it verbatim in \\includegraphics{} "
                "and in latex_to_pdf's image_keys."
            )
        ),
    ],
) -> str:
    """Bind one figure artifact to one image_key for this report. Call once
    per figure before compiling; a second call with an image_key already
    registered for this report returns an error rather than overwriting it."""
    try:
        image = await domain_api.request(
            "POST",
            f"/reports/{report_id}/images",
            json={"file_id": artifact_id, "image_key": image_key},
        )
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc)

    return json.dumps(
        {
            "status": "ok",
            "image_key": image["image_key"],
            "artifact_id": image["file_id"],  # tool result key, kept
        },
        indent=2,
    )
