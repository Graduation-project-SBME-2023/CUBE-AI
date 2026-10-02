from ..._toolkit import is_error_result, tool
from ...common.manifests import READ_ONLY
from .. import _api
from typing import Annotated
from pydantic import Field
from ...common import domain_api


@tool(
    visibility={"agent", "main"},
    **READ_ONLY,
)
async def search_volumes(
    modality: Annotated[str | None, Field(description="The imaging modality to filter volumes by (e.g., 'CT', 'MRI'). If omitted, includes all modalities.")] = None,
    keyword: Annotated[str | None, Field(description="A specific keyword to search for within the volume descriptions.")] = None
):
    """Searches for image volumes based on modality or descriptive keywords."""
    params = {}
    if modality is not None:
        params["modality"] = str(modality)
    if keyword is not None:
        params["keyword"] = keyword
    return await _api.read("/volumes/search/", params=params)


@tool(
    visibility={"agent", "main"},
    **READ_ONLY,
)
async def search_volumes_by_description(
    words_to_include: Annotated[list[str], Field(description="List of keywords that MUST be present in the volume description or body part examined.")],
    words_to_exclude: Annotated[list[str] | None, Field(description="Optional list of keywords that MUST NOT be present in the volume description.")] = None
) -> dict:
    """Search for volumes based on included and excluded words in the series description and body_part_examined fields, and record the search on the population cohort."""

    # 1. Fetch the candidate volumes (a list of dicts)
    params = {"words_to_include": words_to_include}
    if words_to_exclude is not None:
        params["words_to_exclude"] = words_to_exclude
    volume_details = await _api.get_json("/volumes/search/description/", params=params)
    if is_error_result(volume_details):
        return volume_details
    uids_list = [v["uid"] for v in volume_details]  # flat list of candidate UIDs

    # 2. Record the search as selection rationale on the population cohort.
    # Since the database-backed flow there is no `config.json`: the candidate list is not
    # a second copy of the membership (that is validate_population_anatomy's
    # write), only what was searched for and how much came back.
    record_status = "Search recorded on the population cohort."
    try:
        await domain_api.upsert_population_cohort(
            selection_summary={
                "volume_search": {
                    "words_to_include": list(words_to_include),
                    "words_to_exclude": list(words_to_exclude or []),
                    "candidate_count": len(uids_list),
                }
            }
        )
    except domain_api.DomainApiError as exc:
        return {"status": "error", "message": f"The search could not be recorded: {exc}"}

    # 3. Return results without broadcasting via WebSocket
    # (Broadcast will occur AFTER validate_population_anatomy executes)
    return {
        "status": "success",
        "message": f"Found {len(uids_list)} candidate volumes. {record_status} Now call validate_population_anatomy to confirm anatomical alignment.",
        "count": len(uids_list),
        "volumes_detail": volume_details,
    }
