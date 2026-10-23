"""``extract_cohort_labels``: label every volume of the population cohort from
the clinical reports, with the LLM Clinical Adjudicator.

A member is matched to a report by patient identity only. A member with no
report, or whose verdict could not be obtained, is left unlabelled for the
doctor — never recorded as a negative.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from .._toolkit import error_result, tool
from ..common import domain_api, llm, settings
from ..common.emit import emit_view
from ..common.workspace import get_workspace
from .clinical_records import (
    Record,
    bounded_record,
    flatten,
    load_clinical_records,
    match_records,
    normalize_patient_ids,
)
from .label_adjudicator import adjudicate_member, criteria_prompt, normalized_label

logger = logging.getLogger(__name__)

MAX_EVIDENCE_CHARS = 20_000
AUTOMATIC_LABELLING = "automatic"


async def _member_identifiers(member: dict[str, Any], limit: asyncio.Semaphore) -> set[str]:
    """The identifiers a member's report may be filed under: its volume uid
    and its patient's ids. A patient that cannot be read still matches by uid."""
    uid = member["volume_series_instance_uid"]
    identifiers = {uid}
    try:
        async with limit:
            patient = await domain_api.get_patient(member["subject_key"])
    except domain_api.DomainApiError as exc:
        logger.warning("could not read the patient of volume %s: %s", uid, exc)
        return identifiers
    for patient_id in ((patient or {}).get("orthanc_patient_id"), (patient or {}).get("name")):
        identifiers |= normalize_patient_ids(patient_id)
    return identifiers


async def _adjudicate_all(
    matches: dict[str, Record], prompt: str
) -> tuple[dict[str, dict[str, Any]], bool, str]:
    """Every matched member's verdict by volume uid, whether any LLM call
    failed, and the model used. One failed call never drops the others (L6)."""
    endpoint = llm.extraction_endpoint()
    if not matches:
        return {}, False, endpoint.model
    limit = asyncio.Semaphore(settings.llm_concurrency_limit())
    async with llm.openai_client(endpoint) as client:
        outcomes = await asyncio.gather(
            *(
                adjudicate_member(client, endpoint.model, uid, flatten(record), prompt, limit)
                for uid, (_, record) in matches.items()
            ),
            return_exceptions=True,
        )
    failures = [item for item in outcomes if isinstance(item, BaseException)]
    if failures:
        logger.warning(
            "%d of %d LLM label extractions failed; first: %r", len(failures), len(outcomes), failures[0]
        )
    verdicts = {item["volume_uid"]: item for item in outcomes if not isinstance(item, BaseException)}
    return verdicts, bool(failures), endpoint.model


def _unlabelled(uid: str, source: str, reason: str, evidence: str, **provenance: Any) -> dict[str, Any]:
    return {
        "volume_series_instance_uid": uid,
        "has_target_disease": None,
        "label_source": source,
        "label_confidence": "unlabeled",
        "label_provenance": {
            "schema_version": 1,
            "requires_manual_review": True,
            "reason": reason,
            **provenance,
            "source_evidence": evidence,
        },
    }


def _label(
    uid: str, match: Record | None, verdict: dict[str, Any] | None, *, model: str, extracted_at: str
) -> dict[str, Any]:
    """The label row of one member."""
    if match is None:
        return _unlabelled(
            uid, "pending", "no_clinical_report",
            "No medical report found in clinical dataset. Requires manual evaluation by user in Patient Labeling tab.",
        )
    patient_id, record = match
    if verdict is None:
        # The LLM was unreachable: not a negative, not a label at all.
        return {
            **_unlabelled(
                uid, "llm_extraction", "llm_unavailable", "LLM unavailable: connection or auth error.",
                matched_patient_id=patient_id,
            ),
            "clinical_data": bounded_record(record),
        }
    value, confidence = normalized_label(verdict)
    return {
        "volume_series_instance_uid": uid,
        "has_target_disease": value,
        "label_source": "llm_extraction",
        "label_confidence": confidence,
        "label_provenance": {
            "schema_version": 1,
            "requires_manual_review": bool(verdict.get("requires_manual_review")) or value is None,
            "matched_patient_id": patient_id,
            "source_evidence": str(verdict.get("source_evidence") or "")[:MAX_EVIDENCE_CHARS],
            "model": model,
            "extracted_at": extracted_at,
            **({"reason": verdict["failure"]} if verdict.get("failure") else {}),
        },
        "clinical_data": bounded_record(record),
    }


@tool(
    visibility={"agent"},
    side_effects="writes",
    cost="medium",
    execution="inline",
    resources={"llm"},
    data_scopes={"phi_read", "phi_write", "db_write"},
    timeout_s=1800,
)
async def extract_cohort_labels() -> dict[str, Any]:
    """Extracts binary disease-presence labels from clinical reports (JSON or CSV) using an LLM Clinical Adjudicator, for every volume of the population cohort."""
    try:
        cohort = await domain_api.require_population_cohort()
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc)

    cohort_id = cohort["cohort_id"]
    # The Population UI sets the mode before the step starts; "automatic" is
    # what it shows until the doctor chooses. In any other mode the doctor
    # labels the cohort, and no LLM may write a label over that choice.
    labelling_mode = (cohort.get("criteria") or {}).get("labelling_mode", AUTOMATIC_LABELLING)
    if labelling_mode != AUTOMATIC_LABELLING:
        return error_result(
            "manual_labelling",
            f"The cohort is in {labelling_mode!r} labelling mode: the doctor labels the volumes, "
            "so no label was extracted. Skip this step and continue with generate_targets.",
            cohort_id=cohort_id,
        )
    members = cohort.get("members") or []
    if not members:
        return {
            "status": "success",
            "message": (
                "The cohort is empty (0 volumes). Skipping automatic label extraction. "
                "Proceed directly to generate_targets so the user can review the 0 cohort "
                "in the UI and manually select volumes from the dataviewer if desired."
            ),
            "extracted_count": 0,
            "cohort_id": cohort_id,
        }

    lookup_limit = asyncio.Semaphore(settings.llm_concurrency_limit())
    identifiers = await asyncio.gather(*(_member_identifiers(member, lookup_limit) for member in members))
    member_ids = {
        member["volume_series_instance_uid"]: ids for member, ids in zip(members, identifiers)
    }
    # The clinical files can be large: read them off the event loop.
    records, source_path = await asyncio.to_thread(load_clinical_records, get_workspace())
    matches = match_records(records, member_ids)

    criteria = cohort.get("criteria") or {}
    prompt = criteria_prompt(criteria.get("inclusion_criteria", []), criteria.get("exclusion_criteria", []))
    verdicts, llm_unavailable, model = await _adjudicate_all(matches, prompt)

    extracted_at = datetime.now(timezone.utc).isoformat()
    labels = [
        _label(
            member["volume_series_instance_uid"],
            matches.get(member["volume_series_instance_uid"]),
            verdicts.get(member["volume_series_instance_uid"]),
            model=model,
            extracted_at=extracted_at,
        )
        for member in members
    ]
    try:
        await domain_api.write_cohort_labels(cohort_id, labels)
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc, "Labels were not recorded")

    await emit_view(
        "cohort_labels_output",
        {
            "tool": "extract_cohort_labels",
            "status": "completed",
            "extracted_count": len(verdicts),
            "llm_unavailable": llm_unavailable,
            "cohort_id": cohort_id,
        },
    )
    labelled = sum(1 for item in labels if item["has_target_disease"] is not None)
    return {
        "status": "success",
        "message": (
            f"Labelled {labelled} of {len(labels)} cohort volume(s); {len(labels) - len(matches)} had "
            "no matching clinical report and await manual labelling."
        ),
        "llm_unavailable": llm_unavailable,
        "cohort_id": cohort_id,
        "clinical_source": os.path.basename(source_path) if source_path else None,
    }
