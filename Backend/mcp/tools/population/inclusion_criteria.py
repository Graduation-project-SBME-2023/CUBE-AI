import os
from typing import Annotated

import httpx
from pydantic import Field

from .. import mcp
from ..helpers import domain_api


@mcp.tool()
async def write_inclusion_criteria(
    inclusion_criteria: Annotated[list[str], Field(description="List of clinical inclusion criteria rules to define the population.")],
    exclusion_criteria: Annotated[list[str], Field(description="List of clinical exclusion criteria rules to filter out patients.")],
    modality: Annotated[str, Field(description="The imaging modality associated with these criteria (e.g., 'CT', 'MRI').")]
) -> dict:
    """Records the study's inclusion/exclusion criteria and modality on the population cohort, then broadcasts them to the frontend."""
    session_id = os.environ.get("SESSION_ID")
    try:
        cohort = await domain_api.upsert_population_cohort(
            criteria={
                "inclusion_criteria": list(inclusion_criteria),
                "exclusion_criteria": list(exclusion_criteria),
                "modality": modality,
            }
        )
    except domain_api.DomainApiError as exc:
        return {"status": "error", "message": f"Criteria were not recorded: {exc}"}

    try:
        async with httpx.AsyncClient(timeout=5.0) as broadcast_client:
            await broadcast_client.post(
                f'{os.environ.get("MAS_BASE_URL", "http://localhost:8150").rstrip("/")}/ws-broadcast',
                json={
                    "type": "population_output",
                    "payload": {
                        "tool": "write_inclusion_criteria",
                        "status": "completed",
                        "inclusion_criteria": inclusion_criteria,
                        "exclusion_criteria": exclusion_criteria,
                        "modality": modality,
                    },
                    "session_id": session_id,
                },
            )
    except Exception as exc:  # live delivery only; the criteria are committed
        print(f"[write_inclusion_criteria] broadcast failed: {exc}")

    return {
        "status": "success",
        "message": "Criteria recorded on the population cohort.",
        "cohort_id": cohort.get("cohort_id"),
    }
