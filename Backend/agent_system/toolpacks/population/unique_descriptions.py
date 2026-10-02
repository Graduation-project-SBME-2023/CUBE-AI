from .._toolkit import is_error_result, tool
from ..common.manifests import READ_ONLY
from ..db_reads import _api

@tool(
    visibility={"agent"},
    timeout_s=45,
    **READ_ONLY,
)
async def get_unique_series_descriptions() -> dict:
    """
    Retrieve a list of all unique volume series descriptions available in the database.
    
    Use this tool to discover what descriptive terms actually exist in the dataset. 
    This is highly useful before performing a search to see exactly how protocols or 
    body parts are named (e.g., checking if it's 'CT Head' vs 'Head CT').
    """
    descriptions = await _api.get_json("/volumes/descriptions/unique/")
    if is_error_result(descriptions):
        return descriptions
    return {
        "status": "success",
        "count": len(descriptions),
        "unique_descriptions": descriptions,
    }
