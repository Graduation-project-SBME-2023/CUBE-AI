from .._toolkit import tool
from ..common.manifests import WRITES
from ..common.emit import emit
from ..common import domain_api


@tool(
    visibility={"agent"},
    **WRITES,
)
async def save_final_output(data: dict):
    """Record the population agent's selected volumes as the cohort membership."""
    datasetlist = [str(uid) for uid in (data.get("datasetlist") or [])]
    try:
        cohort = await domain_api.upsert_population_cohort()
        cohort = await domain_api.set_cohort_members(cohort["cohort_id"], datasetlist)
    except domain_api.DomainApiError as exc:
        return {"status": "error", "message": f"The cohort was not recorded: {exc}"}

    patients = {member["subject_key"] for member in cohort.get("members") or []}
    message = f"Selected {len(datasetlist)} volumes from {len(patients)} patients."
    emit("tool_log", message)
    return {"status": "success", "cohort_id": cohort["cohort_id"]}
