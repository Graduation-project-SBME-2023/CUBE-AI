"""Anatomy Validation Gate for the Population Agent."""

import os
from typing import Annotated
from pydantic import Field
from .. import mcp
from ..helpers import domain_api
from internal_emitter import broadcast_ws_message


# ── Anatomy group mapping ───────────────────────────────────────────────────
# Maps high-level anatomical regions to lists of keywords that would appear
# in DICOM series_description or body_part_examined fields.
ANATOMY_GROUPS = {
    "chest": [
        "chest", "thorax", "lung", "pulmonary", "thoracic", "mediastin",
        "bronch", "pleura", "trachea",
    ],
    "abdomen": [
        "abdomen", "abdominal", "abd", "kidney", "renal", "liver",
        "hepatic", "spleen", "splenic", "pancrea", "bowel", "intestin",
        "colon", "pelvis", "pelvic", "gallbladder", "biliary", "adrenal",
        "retroperitoneal", "mesentery",
    ],
    "head": [
        "head", "brain", "cranial", "neuro", "cerebr", "skull",
        "intracranial", "orbit", "sella", "temporal",
    ],
    "neck": [
        "neck", "cervical", "thyroid", "laryn", "pharyn", "parotid",
        "submandibular",
    ],
    "spine": [
        "spine", "spinal", "vertebr", "lumbar", "sacral", "thoracolumbar",
        "disc", "cord",
    ],
    "extremity": [
        "extremity", "arm", "leg", "femur", "tibia", "humerus", "knee",
        "ankle", "wrist", "hand", "foot", "shoulder", "hip", "elbow",
        "musculoskeletal",
    ],
    "cardiac": [
        "cardiac", "heart", "coronary", "aort", "myocard", "pericardi",
    ],
    "breast": [
        "breast", "mammog", "mammo", "axilla",
    ],
}


def _resolve_anatomy_group(text: str) -> tuple[str | None, list[str]]:
    """Resolve a free-text body region to an anatomy group and its keywords."""
    text_lower = text.lower().strip()
    for group, keywords in ANATOMY_GROUPS.items():
        # Direct group name match
        if group in text_lower:
            return group, keywords
        # Keyword match
        for kw in keywords:
            if kw in text_lower or text_lower in kw:
                return group, keywords
    return None, []


async def _save_and_broadcast_cohort(
    validated_uids: list[str],
    validation: dict | None = None,
) -> dict:
    """Records the validated cohort (even an empty 0-cohort) as the population cohort's membership and broadcasts it so the Population tab renders the post-validation result."""
    session_id = os.environ.get("SESSION_ID")
    cohort = await domain_api.upsert_population_cohort(
        selection_summary={
            "anatomy_validation": {
                "anatomy_validated": True,
                **(validation or {}),
            }
        }
    )
    await domain_api.set_cohort_members(cohort["cohort_id"], validated_uids)

    await broadcast_ws_message(
        "population_output",
        {
            "tool": "validate_population_anatomy",
            "status": "completed",
            "count": len(validated_uids),
            "uid_list": validated_uids,
            "cohort_id": cohort["cohort_id"],
        },
        session_id,
    )

    return {"cohort_updated": True, "cohort_id": cohort["cohort_id"]}


@mcp.tool()
async def validate_population_anatomy(
    hypothesis_body_region: Annotated[
        str,
        Field(description=(
            "The anatomical region specified in the research hypothesis "
            "(e.g., 'lung', 'kidney', 'brain', 'abdomen')."
        ))
    ],
    volume_details: Annotated[
        list[dict],
        Field(description=(
            "List of volume detail dicts from search_volumes_by_description. "
            "Each dict must have 'uid', 'series_description', "
            "'body_part_examined', and 'modality' keys."
        ))
    ],
) -> dict:
    """Validates that candidate volumes anatomically match the research hypothesis."""
    if not volume_details:
        save_meta = await _save_and_broadcast_cohort(
            [], {"hypothesis_body_region": hypothesis_body_region, "total_input": 0}
        )
        return {
            "status": "success",
            "message": (
                "No candidate volumes were returned by the search (0 cohort). "
                "A 0-volume cohort has been recorded and sent to the interactive UI. "
                "DO NOT STOP execution or raise a pipeline error! Instead: "
                "(1) Note in your scratchpad/summary in the agent execution tab that 0 matching studies were automatically found. "
                "(2) Proceed directly to Step 5 (if available) and Step 6 (generate_targets). "
                "When generate_targets runs, it will remain at the Human-in-the-Loop UI review gate until the doctor selects and labels at least one volume. Never hand off a 0-size cohort."
            ),
            "validated_uids": [],
            "rejected_volumes": [],
            "total_input": 0,
            "total_validated": 0,
            "total_rejected": 0,
            **save_meta,
        }

    # ── Resolve hypothesis anatomy ──────────────────────────────────────
    matched_group, matched_keywords = _resolve_anatomy_group(hypothesis_body_region)

    if not matched_group:
        # Cannot resolve anatomy — pass everything with a warning
        validated_uids = [v["uid"] for v in volume_details]
        save_meta = await _save_and_broadcast_cohort(
            validated_uids,
            {
                "hypothesis_body_region": hypothesis_body_region,
                "matched_anatomy_group": None,
                "total_input": len(volume_details),
                "warning": "anatomy region could not be resolved; no volume was filtered",
            },
        )
        return {
            "status": "warning",
            "message": (
                f"Could not resolve '{hypothesis_body_region}' to a known anatomy "
                f"group. All {len(volume_details)} volumes passed through without "
                f"filtering. Manually verify they match your hypothesis."
            ),
            "matched_anatomy_group": None,
            "validated_uids": validated_uids,
            "validated_volumes": volume_details,
            "rejected_volumes": [],
            "total_input": len(volume_details),
            "total_validated": len(volume_details),
            "total_rejected": 0,
            **save_meta,
        }

    # ── Classify each volume ────────────────────────────────────────────
    validated = []
    rejected = []

    for vol in volume_details:
        uid = vol.get("uid", "")
        desc = (vol.get("series_description") or "").lower()
        body_part = (vol.get("body_part_examined") or "").lower()
        combined_text = f"{desc} {body_part}"

        # Check if any keyword from the matched anatomy group appears
        is_match = any(kw in combined_text for kw in matched_keywords)

        # Check for conflicting anatomy (clearly belongs to a DIFFERENT group)
        conflicting_group = None
        if not is_match:
            for other_group, other_keywords in ANATOMY_GROUPS.items():
                if other_group == matched_group:
                    continue
                if any(kw in combined_text for kw in other_keywords):
                    conflicting_group = other_group
                    break

        if is_match:
            validated.append(vol)
        elif conflicting_group:
            rejected.append({
                "uid": uid,
                "series_description": vol.get("series_description", ""),
                "body_part_examined": vol.get("body_part_examined", ""),
                "reason": (
                    f"Anatomy MISMATCH: volume belongs to '{conflicting_group}' "
                    f"region, but hypothesis requires '{matched_group}'"
                ),
            })
        else:
            # No clear anatomy signal — include but it's ambiguous
            validated.append(vol)

    # ── Save and Broadcast validated UIDs ───────────────────────────────
    validated_uids = [v["uid"] for v in validated]
    save_meta = await _save_and_broadcast_cohort(
        validated_uids,
        {
            "hypothesis_body_region": hypothesis_body_region,
            "matched_anatomy_group": matched_group,
            "total_input": len(volume_details),
            "rejected_volumes": rejected[:500],
        },
    )

    # ── Build response ──────────────────────────────────────────────────
    result = {
        "matched_anatomy_group": matched_group,
        "anatomy_keywords_used": matched_keywords,
        "validated_uids": validated_uids,
        "validated_volumes": validated,
        "rejected_volumes": rejected,
        "total_input": len(volume_details),
        "total_validated": len(validated),
        "total_rejected": len(rejected),
        **save_meta,
    }

    if not validated:
        result["status"] = "success"
        reasons_str = ", ".join(set(r["reason"].split("'")[1] for r in rejected if "reason" in r and "'" in r["reason"]))
        result["message"] = (
            f"0 matching volumes found for anatomy ('{matched_group}'). "
            f"All {len(volume_details)} candidate volumes were filtered out because they belong to different body regions "
            f"({reasons_str}). "
            f"A 0-volume cohort has been recorded and sent to the interactive UI. "
            f"DO NOT STOP execution or raise a pipeline error! Instead: "
            f"(1) Raise a scratchpad flag with level='warning' (or mention in your execution summary) that 0 matching studies were automatically found for this anatomy. "
            f"(2) Proceed directly to Step 5 (if available) and Step 6 (generate_targets). "
            f"When generate_targets runs, it will remain at the Human-in-the-Loop UI review gate until the doctor selects and labels at least one volume. Never hand off a 0-size cohort."
        )
    elif rejected:
        result["status"] = "success"
        result["message"] = (
            f"Filtered out {len(rejected)} volumes that don't match "
            f"'{matched_group}' anatomy. {len(validated)} volumes passed validation and were sent to the UI."
        )
    else:
        result["status"] = "success"
        result["message"] = (
            f"All {len(validated)} volumes match the "
            f"'{matched_group}' anatomy. Sent to the UI. Proceed with confidence."
        )

    return result
