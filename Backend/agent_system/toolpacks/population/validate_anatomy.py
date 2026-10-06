"""``validate_population_anatomy``: the anatomy gate of the population step.

The candidates the volume search found are checked against the hypothesis'
body region (``anatomy.py``), and the validated ones become the population
cohort's membership — even an empty one, which the doctor then fills in the UI.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from .._toolkit import error_result, tool
from ..common import domain_api
from ..common.emit import emit_view
from ..common.manifests import WRITES
from .anatomy import MIN_REGION_LENGTH, classify, resolve_anatomy_group

# What the agent must do when no volume is left; it must never stop the pipeline.
_EMPTY_COHORT_STEPS = (
    "A 0-volume cohort has been recorded and sent to the interactive UI. "
    "DO NOT STOP execution or raise a pipeline error! Instead: "
    "(1) {note} "
    "(2) Proceed directly to Step 5 (if available) and Step 6 (generate_targets). "
    "When generate_targets runs, it will remain at the Human-in-the-Loop UI review gate until "
    "the doctor selects and labels at least one volume. Never hand off a 0-size cohort."
)


async def _record_cohort(validated_uids: list[str], validation: dict[str, Any]) -> dict[str, Any]:
    """Record the validated volumes (even none) as the population cohort's
    membership, and show the result in the Population tab."""
    cohort = await domain_api.upsert_population_cohort(
        selection_summary={"anatomy_validation": {"anatomy_validated": True, **validation}}
    )
    await domain_api.set_cohort_members(cohort["cohort_id"], validated_uids)
    await emit_view(
        "population_output",
        {
            "tool": "validate_population_anatomy",
            "status": "completed",
            "count": len(validated_uids),
            "uid_list": validated_uids,
            "cohort_id": cohort["cohort_id"],
        },
    )
    return {"cohort_updated": True, "cohort_id": cohort["cohort_id"]}


async def _no_candidates(region: str) -> dict[str, Any]:
    recorded = await _record_cohort([], {"hypothesis_body_region": region, "total_input": 0})
    return {
        "status": "success",
        "message": "No candidate volumes were returned by the search (0 cohort). " + _EMPTY_COHORT_STEPS.format(
            note=(
                "Note in your scratchpad/summary in the agent execution tab that 0 matching "
                "studies were automatically found."
            )
        ),
        "validated_uids": [],
        "rejected_volumes": [],
        "total_input": 0,
        "total_validated": 0,
        "total_rejected": 0,
        **recorded,
    }


async def _unresolved_region(region: str, volumes: list[dict[str, Any]]) -> dict[str, Any]:
    """The region names no known group: every volume passes, with a warning."""
    uids = [volume["uid"] for volume in volumes]
    recorded = await _record_cohort(
        uids,
        {
            "hypothesis_body_region": region,
            "matched_anatomy_group": None,
            "total_input": len(volumes),
            "warning": "anatomy region could not be resolved; no volume was filtered",
        },
    )
    return {
        "status": "warning",
        "message": (
            f"Could not resolve '{region}' to a known anatomy group. All {len(volumes)} volumes "
            "passed through without filtering. Manually verify they match your hypothesis."
        ),
        "matched_anatomy_group": None,
        "validated_uids": uids,
        "validated_volumes": volumes,
        "rejected_volumes": [],
        "total_input": len(volumes),
        "total_validated": len(volumes),
        "total_rejected": 0,
        **recorded,
    }


async def _validate(region: str, volumes: list[dict[str, Any]]) -> dict[str, Any]:
    if not volumes:
        return await _no_candidates(region)
    group, keywords = resolve_anatomy_group(region)
    if not group:
        return await _unresolved_region(region, volumes)

    result = classify(volumes, group, keywords)
    recorded = await _record_cohort(
        result.validated_uids,
        {
            "hypothesis_body_region": region,
            "matched_anatomy_group": group,
            "total_input": len(volumes),
            "rejected_volumes": result.rejected[:500],
        },
    )
    if not result.validated:
        message = (
            f"0 matching volumes found for anatomy ('{group}'). All {len(volumes)} candidate "
            "volumes were filtered out because they belong to different body regions "
            f"({', '.join(sorted(result.conflicting_groups))}). "
        ) + _EMPTY_COHORT_STEPS.format(
            note=(
                "Raise a scratchpad flag with level='warning' (or mention in your execution "
                "summary) that 0 matching studies were automatically found for this anatomy."
            )
        )
    elif result.rejected:
        message = (
            f"Filtered out {len(result.rejected)} volumes that don't match '{group}' anatomy. "
            f"{len(result.validated)} volumes passed validation and were sent to the UI."
        )
    else:
        message = (
            f"All {len(result.validated)} volumes match the '{group}' anatomy. "
            "Sent to the UI. Proceed with confidence."
        )
    return {
        "matched_anatomy_group": group,
        "anatomy_keywords_used": keywords,
        "validated_uids": result.validated_uids,
        "validated_volumes": result.validated,
        "rejected_volumes": result.rejected,
        "total_input": len(volumes),
        "total_validated": len(result.validated),
        "total_rejected": len(result.rejected),
        **recorded,
        "status": "success",
        "message": message,
    }


@tool(
    visibility={"agent"},
    data_scopes={"db_write", "phi_write"},
    timeout_s=90,
    **WRITES,
)
async def validate_population_anatomy(
    hypothesis_body_region: Annotated[
        str,
        Field(description=(
            "The anatomical region specified in the research hypothesis "
            "(e.g., 'lung', 'kidney', 'brain', 'abdomen')."
        )),
    ],
    volume_details: Annotated[
        list[dict],
        Field(description=(
            "List of volume detail dicts from search_volumes_by_description. "
            "Each dict must have 'uid', 'series_description', "
            "'body_part_examined', and 'modality' keys."
        )),
    ],
) -> dict[str, Any]:
    """Validates that candidate volumes anatomically match the research hypothesis."""
    if len(hypothesis_body_region.strip()) < MIN_REGION_LENGTH:
        return error_result(
            "invalid_args",
            f"hypothesis_body_region {hypothesis_body_region!r} is too short to name a body region "
            f"(at least {MIN_REGION_LENGTH} characters, e.g. 'lung', 'kidney', 'brain').",
        )
    missing = [index for index, volume in enumerate(volume_details) if not volume.get("uid")]
    if missing:
        return error_result(
            "invalid_args",
            f"volume_details entries {missing[:10]} have no 'uid'; pass the dicts "
            "search_volumes_by_description returned.",
        )
    try:
        return await _validate(hypothesis_body_region, volume_details)
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc, "The validated cohort was not recorded")
