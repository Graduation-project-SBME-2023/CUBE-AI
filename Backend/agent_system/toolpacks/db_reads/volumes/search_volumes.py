from ..._toolkit import tool
from ...common.manifests import READ_ONLY
from typing import Annotated
from pydantic import Field
import httpx
from ...common.service_urls import CONTROL_API_URL
from ...common import domain_api

base_url = CONTROL_API_URL


@tool(
    visibility={"agent", "main"},
    **READ_ONLY,
)
async def search_volumes(
    modality: Annotated[str | None, Field(description="The imaging modality to filter volumes by (e.g., 'CT', 'MRI'). If omitted, includes all modalities.")] = None,
    keyword: Annotated[str | None, Field(description="A specific keyword to search for within the volume descriptions.")] = None
):
    """Searches for image volumes based on modality or descriptive keywords."""
    async with httpx.AsyncClient() as client:
        params = {}
        if modality is not None:
            params["modality"] = str(modality)
        if keyword is not None:
            params["keyword"] = keyword
            
        response = await client.get(
            base_url + "/volumes/search/",
            params=params
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return {"error": "Invalid JSON returned", "raw": response.text}

        return {"data": data}


@tool(
    visibility={"agent", "main"},
    **READ_ONLY,
)
async def search_volumes_by_description(
    words_to_include: Annotated[list[str], Field(description="List of keywords that MUST be present in the volume description or body part examined.")],
    words_to_exclude: Annotated[list[str] | None, Field(description="Optional list of keywords that MUST NOT be present in the volume description.")] = None
) -> dict:
    """Search for volumes based on included and excluded words in the series description and body_part_examined fields, and record the search on the population cohort."""

    volume_details = []  # list of dicts from the API
    uids_list = []       # flat list of candidate UIDs

    # 1. Fetch the data from the FastAPI endpoint
    async with httpx.AsyncClient() as client:
        params = {"words_to_include": words_to_include}
        if words_to_exclude is not None:
            params["words_to_exclude"] = words_to_exclude

        try:
            response = await client.get(
                base_url + "/volumes/search/description/",
                params=params
            )
            response.raise_for_status()
            volume_details = response.json()  # list of dicts
            uids_list = [v["uid"] for v in volume_details]

        except httpx.HTTPStatusError as e:
            return {"status": "error", "message": f"HTTP error occurred: {str(e)}"}
        except ValueError:
            return {"status": "error", "message": "Invalid JSON returned from API", "raw": response.text}
        except Exception as e:
            return {"status": "error", "message": f"An unexpected API error occurred: {str(e)}"}

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
