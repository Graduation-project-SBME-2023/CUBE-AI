from typing import Annotated

from pydantic import Field

from .._toolkit import tool
from ..common.manifests import WRITES
from ..common import domain_api
from ..common.emit import emit_view


@tool(
    visibility={"agent"},
    data_scopes={"db_write"},
    timeout_s=60,
    **WRITES,
)
async def write_inclusion_criteria(
    inclusion_criteria: Annotated[list[str], Field(description="List of clinical inclusion criteria rules to define the population.")],
    exclusion_criteria: Annotated[list[str], Field(description="List of clinical exclusion criteria rules to filter out patients.")],
    modality: Annotated[str, Field(description="The imaging modality associated with these criteria (e.g., 'CT', 'MRI').")]
) -> dict:
    """Records the study's inclusion/exclusion criteria and modality on the population cohort, then broadcasts them to the frontend."""
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

    await emit_view(
        "population_output",
        {
            "tool": "write_inclusion_criteria",
            "status": "completed",
            "inclusion_criteria": inclusion_criteria,
            "exclusion_criteria": exclusion_criteria,
            "modality": modality,
        },
    )

    return {
        "status": "success",
        "message": "Criteria recorded on the population cohort.",
        "cohort_id": cohort.get("cohort_id"),
    }
