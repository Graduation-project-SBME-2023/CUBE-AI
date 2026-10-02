import os
import json
import csv
import asyncio
import logging
from datetime import datetime, timezone

from .._toolkit import tool
from ..common.manifests import WRITES
from ..common import domain_api
from storage_paths import workspace_subpath
from ..common.emit import emit_view
from ..common import llm, settings
from ..common.workspace import get_workspace

logger = logging.getLogger(__name__)

# Confidence keeps the observed categories; "manual override" is a label
# SOURCE, not a confidence.
_CONFIDENCES = {"high", "medium", "low", "unlabeled"}


def _normalize_patient_ids(pid: str | None) -> set[str]:
    if not pid:
        return set()
    cleaned = str(pid).strip()
    results = {cleaned, cleaned.lower()}
    lower = cleaned.lower()
    for prefix in ("kits-", "kits23-", "kits23_", "case-", "case_"):
        if lower.startswith(prefix):
            suffix = lower[len(prefix):]
            results.add(f"case_{suffix}")
            results.add(f"KiTS_{suffix}")
            results.add(f"KiTS-{suffix}")
            results.add(f"KITS-{suffix}")
            results.add(suffix)
    if cleaned.isdigit():
        results.add(f"case_{cleaned}")
        results.add(f"KiTS-{cleaned}")
        results.add(f"KITS-{cleaned}")
    return results


async def _extract_with_llm(client, model_name: str, flattened_text: str, context_prompt: str) -> dict:
    """
    Independent LLM call for a single patient record.
    Strips JSON fences, ensures source_evidence exists, retries once on failure.
    """
    messages = [
        {"role": "system", "content": (
            f"You are a Clinical Adjudicator.\n\n{context_prompt}\n\n"
            "Output pure JSON only, in this exact format:\n"
            "{\n"
            '  "has_target_disease": true, false, or null,\n'
            '  "confidence": "high" | "medium" | "low" | "unlabeled",\n'
            '  "source_evidence": "Detailed reasoning here"\n'
            "}\n"
            "Do not include Markdown fences."
        )},
        {"role": "user", "content": f"Patient Record:\n{flattened_text}"}
    ]

    def _parse_and_validate(response_text: str) -> dict | None:
        try:
            clean_text = response_text.strip()
            if clean_text.startswith("```"):
                lines = clean_text.splitlines()
                if len(lines) >= 2 and lines[0].startswith("```"):
                    if lines[-1].startswith("```"):
                        clean_text = "\n".join(lines[1:-1])
                    else:
                        clean_text = "\n".join(lines[1:])
            parsed = json.loads(clean_text)
            if not parsed.get("source_evidence") or str(parsed.get("source_evidence")).strip() == "":
                return None
            return parsed
        except Exception:
            return None

    response = await client.chat.completions.create(
        model=model_name,
        messages=messages,
        temperature=0.0
    )
    content = response.choices[0].message.content
    parsed = _parse_and_validate(content)
    if parsed:
        return parsed

    # Retry attempt — remind the model of the required format
    messages.append({"role": "assistant", "content": content})
    messages.append({
        "role": "user",
        "content": (
            "Your response was not valid JSON or lacked mandatory source_evidence. "
            "Return ONLY the JSON object with has_target_disease, confidence, and source_evidence. "
            "Do not include any other text."
        )
    })
    response2 = await client.chat.completions.create(
        model=model_name,
        messages=messages,
        temperature=0.0
    )
    parsed2 = _parse_and_validate(response2.choices[0].message.content)
    if parsed2:
        return parsed2

    # Both attempts failed. The honest result is "not labelled" — a failed
    # extraction is not a negative finding, and current never records one as such.
    return {
        "has_target_disease": None,
        "confidence": "unlabeled",
        "requires_manual_review": True,
        "failure": "invalid_llm_response",
        "source_evidence": "LLM extraction failed: invalid JSON response",
    }


async def _process_patient_record(
    client,
    model_name: str,
    volume_uid: str,
    flattened_text: str,
    context_prompt: str,
    sem: asyncio.Semaphore
) -> dict:
    async with sem:
        llm_result = await _extract_with_llm(client, model_name, flattened_text, context_prompt)
        # Re-attach the volume identity — never let the LLM overwrite it
        llm_result["volume_uid"] = volume_uid
        return llm_result


def _load_clinical_records(workspace_dir: str | None) -> tuple[list[tuple[str, dict]], str | None]:
    """Read the clinical import source."""
    candidates: list[str] = []
    if workspace_dir:
        candidates += [
            str(workspace_subpath(workspace_dir, "clinical_data.json")),
            str(workspace_subpath(workspace_dir, "clinical_data.csv")),
        ]
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    candidates += [
        os.path.join(data_dir, "kits23_reduced.json"),
        os.path.join(data_dir, "kits23_reduced.csv"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        records: list[tuple[str, dict]] = []
        if path.endswith(".json"):
            with open(path, "r", encoding="utf-8") as stream:
                data = json.load(stream)
            if isinstance(data, list):
                for item in data:
                    patient_id = item.get("case_id") or item.get("patient_id")
                    if patient_id:
                        records.append((str(patient_id), item))
            elif isinstance(data, dict):
                for key, value in data.items():
                    if key:
                        records.append((str(key), value if isinstance(value, dict) else {"report": value}))
        else:
            with open(path, "r", encoding="utf-8") as stream:
                for row in csv.DictReader(stream):
                    patient_id = row.get("case_id") or row.get("patient_id")
                    if patient_id:
                        records.append((str(patient_id), dict(row)))
        return records, path
    return [], None


def _flatten(record: dict) -> str:
    return " | ".join(
        f"{key}: {value}" for key, value in record.items() if key not in ("case_id", "patient_id")
    )


def _bounded_record(record: dict) -> dict:
    """The per-member clinical evidence, bounded."""
    text = json.dumps(record, ensure_ascii=False, default=str)
    if len(text.encode("utf-8")) > 200 * 1024:
        return {"schema_version": 1, "truncated": True, "text": text[: 200 * 1024 // 2]}
    return {"schema_version": 1, "record": json.loads(text)}


@tool(
    visibility={"agent"},
    side_effects="writes",
    cost="medium",
    execution="inline",
    resources={"llm"},
    data_scopes={"phi_read", "phi_write", "db_write"},
    timeout_s=1800,
)
async def extract_cohort_labels() -> dict:
    """Extracts binary disease-presence labels from clinical reports (JSON or CSV) using an LLM Clinical Adjudicator, for every volume of the population cohort."""
    workspace_dir = get_workspace()
    try:
        cohort = await domain_api.require_population_cohort()
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc)

    cohort_id = cohort["cohort_id"]
    criteria = cohort.get("criteria") or {}
    inclusion_criteria = criteria.get("inclusion_criteria", [])
    exclusion_criteria = criteria.get("exclusion_criteria", [])
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

    # ── Resolve each member's patient identifiers ───────────────────────────────
    member_ids: dict[str, set[str]] = {}
    member_patient: dict[str, dict] = {}
    for member in members:
        uid = member["volume_series_instance_uid"]
        identifiers = {uid}
        try:
            patient = await domain_api.request(
                "GET", f"/patients/{member['subject_key']}", allow_404=True
            )
        except domain_api.DomainApiError as exc:
            # The volume can still match by its own UID; the doctor labels the rest.
            logger.warning("could not read the patient of volume %s: %s", uid, exc)
            patient = None
        if patient:
            member_patient[uid] = patient
            for pid in (patient.get("orthanc_patient_id"), patient.get("name")):
                identifiers.update(_normalize_patient_ids(pid))
        member_ids[uid] = identifiers

    # The clinical files can be large: read them off the event loop.
    records, source_path = await asyncio.to_thread(_load_clinical_records, workspace_dir)

    # ── Match records to members by patient identity only ───────────────────────
    matches: dict[str, tuple[str, dict]] = {}
    for patient_id, record in records:
        keys = _normalize_patient_ids(patient_id) | {patient_id}
        for uid, identifiers in member_ids.items():
            if uid not in matches and keys & identifiers:
                matches[uid] = (patient_id, record)

    context_prompt = (
        "You are evaluating clinical records against the following study criteria:\n"
        f"INCLUSION CRITERIA: {json.dumps(inclusion_criteria)}\n"
        f"EXCLUSION CRITERIA: {json.dumps(exclusion_criteria)}\n\n"
        "You must determine if the patient has the target disease/condition specified "
        "in the inclusion criteria. Follow these strict labeling rules:\n"
        "1. If the medical report is relevant to the target organ/condition and confirms the presence of the target disease, output 'has_target_disease': true.\n"
        "2. If the medical report is relevant to the target organ/condition and explicitly confirms the target disease is absent (e.g., normal or healthy reference findings for that organ), output 'has_target_disease': false.\n"
        "3. If the medical report discusses an unrelated organ or body region, or completely lacks relevant clinical information regarding the target disease/hypothesis, do NOT label it! Leave it unlabeled by outputting 'has_target_disease': null, set 'confidence': 'unlabeled', and clearly document in 'source_evidence' why the report is unrelated or lacks sufficient clinical evidence."
    )

    results: list[dict] = []
    llm_unavailable = False
    endpoint = llm.extraction_endpoint()
    model_name = endpoint.model
    if matches:
        from openai import AsyncOpenAI

        client_llm = AsyncOpenAI(base_url=endpoint.base_url, api_key=endpoint.api_key)
        sem = asyncio.Semaphore(settings.llm_concurrency_limit())
        batch = await asyncio.gather(
            *[
                _process_patient_record(client_llm, model_name, uid, _flatten(record), context_prompt, sem)
                for uid, (_, record) in matches.items()
            ],
            return_exceptions=True,
        )
        # Keep every label that came back: one failed call must not drop the
        # others (L6). The failed volumes are recorded as "llm_unavailable" below.
        failures = [item for item in batch if isinstance(item, BaseException)]
        results = [item for item in batch if not isinstance(item, BaseException)]
        if failures:
            llm_unavailable = True
            logger.warning(
                "%d of %d LLM label extractions failed; first: %r",
                len(failures),
                len(batch),
                failures[0],
            )

    extracted_at = datetime.now(timezone.utc).isoformat()
    by_uid = {item["volume_uid"]: item for item in results}
    labels: list[dict] = []
    for member in members:
        uid = member["volume_series_instance_uid"]
        match = matches.get(uid)
        if match is None:
            # No report: the doctor labels this volume by hand.
            labels.append({
                "volume_series_instance_uid": uid,
                "has_target_disease": None,
                "label_source": "pending",
                "label_confidence": "unlabeled",
                "label_provenance": {
                    "schema_version": 1,
                    "requires_manual_review": True,
                    "reason": "no_clinical_report",
                    "source_evidence": "No medical report found in clinical dataset. Requires manual evaluation by user in Patient Labeling tab.",
                },
            })
            continue
        patient_id, record = match
        outcome = by_uid.get(uid)
        if outcome is None:
            # The LLM was unreachable: not a negative, not a label at all.
            labels.append({
                "volume_series_instance_uid": uid,
                "has_target_disease": None,
                "label_source": "llm_extraction",
                "label_confidence": "unlabeled",
                "label_provenance": {
                    "schema_version": 1,
                    "requires_manual_review": True,
                    "reason": "llm_unavailable",
                    "matched_patient_id": patient_id,
                    "source_evidence": "LLM unavailable: connection or auth error.",
                },
                "clinical_data": _bounded_record(record),
            })
            continue
        value = outcome.get("has_target_disease")
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            value = value.strip().lower() == "true"
        if not isinstance(value, bool):
            value = None
        confidence = str(outcome.get("confidence") or "").strip().lower()
        if value is None or confidence not in _CONFIDENCES:
            confidence = "unlabeled" if value is None else "low"
        labels.append({
            "volume_series_instance_uid": uid,
            "has_target_disease": value,
            "label_source": "llm_extraction",
            "label_confidence": confidence,
            "label_provenance": {
                "schema_version": 1,
                "requires_manual_review": bool(outcome.get("requires_manual_review")) or value is None,
                "matched_patient_id": patient_id,
                "source_evidence": str(outcome.get("source_evidence") or "")[:20000],
                "model": model_name,
                "extracted_at": extracted_at,
                **({"reason": outcome["failure"]} if outcome.get("failure") else {}),
            },
            "clinical_data": _bounded_record(record),
        })

    try:
        await domain_api.write_cohort_labels(cohort_id, labels)
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc, "Labels were not recorded")

    await emit_view(
        "cohort_labels_output",
        {
            "tool": "extract_cohort_labels",
            "status": "completed",
            "extracted_count": len(results),
            "llm_unavailable": llm_unavailable,
            "cohort_id": cohort_id,
        },
    )

    return {
        "status": "success",
        "message": (
            f"Labelled {sum(1 for item in labels if item['has_target_disease'] is not None)} of "
            f"{len(labels)} cohort volume(s); {len(labels) - len(matches)} had no matching "
            "clinical report and await manual labelling."
        ),
        "llm_unavailable": llm_unavailable,
        "cohort_id": cohort_id,
        "clinical_source": os.path.basename(source_path) if source_path else None,
    }
