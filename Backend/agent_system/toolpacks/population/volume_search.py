"""``search_volumes_by_description``: the population step's candidate search.

It reads the candidate volumes and records what was searched for on the
population cohort (selection rationale, not membership: the membership is
``validate_population_anatomy``'s write).
"""

from typing import Annotated, Any

from pydantic import Field

from .._toolkit import is_error_result, tool
from ..common import domain_api
from ..common.manifests import WRITES
from ..db_reads import _api


@tool(
    visibility={"agent"},
    data_scopes={"phi_read", "db_write"},
    timeout_s=90,
    **WRITES,
)
async def search_volumes_by_description(
    words_to_include: Annotated[list[str], Field(description="List of keywords that MUST be present in the volume description or body part examined.")],
    words_to_exclude: Annotated[list[str] | None, Field(description="Optional list of keywords that MUST NOT be present in the volume description.")] = None,
) -> dict[str, Any]:
    """Search for volumes based on included and excluded words in the series description and body_part_examined fields, and record the search on the population cohort."""
    volumes = await _api.get_json(
        "/volumes/search/description/",
        params=_api.query(words_to_include=words_to_include, words_to_exclude=words_to_exclude),
    )
    if is_error_result(volumes):
        return volumes

    try:
        await domain_api.upsert_population_cohort(
            selection_summary={
                "volume_search": {
                    "words_to_include": list(words_to_include),
                    "words_to_exclude": list(words_to_exclude or []),
                    "candidate_count": len(volumes),
                }
            }
        )
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc, "The search could not be recorded")

    # No view update here: the population view refreshes after
    # validate_population_anatomy has filtered the candidates.
    return {
        "status": "success",
        "message": (
            f"Found {len(volumes)} candidate volumes. Search recorded on the population cohort. "
            "Now call validate_population_anatomy to confirm anatomical alignment."
        ),
        "count": len(volumes),
        "volumes_detail": volumes,
    }
