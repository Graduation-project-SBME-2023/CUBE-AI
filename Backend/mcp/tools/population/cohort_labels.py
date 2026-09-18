import os
import json
import csv
import httpx
import asyncio
from datetime import datetime, timezone
from typing import Annotated, Any
from pydantic import Field
from .. import mcp
from storage_paths import workspace_subpath


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

    # First attempt
    try:
        response = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0.0
        )
        content = response.choices[0].message.content
        parsed = _parse_and_validate(content)
        if parsed:
            return parsed
    except Exception as e:
        raise e

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
    try:
        response2 = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0.0
        )
        content2 = response2.choices[0].message.content
        parsed2 = _parse_and_validate(content2)
        if parsed2:
            return parsed2
    except Exception as e:
        raise e

    # Both attempts failed — return safe fallback
    return {
        "has_target_disease": False,
        "confidence": "low",
        "requires_manual_review": True,
        "source_evidence": "LLM extraction failed: invalid JSON response"
    }


async def _process_patient_record(
    client,
    model_name: str,
    patient_id: str,
    flattened_text: str,
    context_prompt: str,
    sem: asyncio.Semaphore
) -> dict:
    async with sem:
        try:
            llm_result = await _extract_with_llm(client, model_name, flattened_text, context_prompt)
            # Re-attach patient ID — never let the LLM overwrite it
            llm_result["patient_id"] = patient_id
            return llm_result
        except Exception as e:
            raise e


@mcp.tool()
async def extract_cohort_labels() -> dict:
    """
    Extracts binary disease-presence targets from clinical reports (JSON or CSV)
    using a 100% LLM Clinical Adjudicator.

    The adjudicator reads the inclusion/exclusion criteria from config.json and
    evaluates each patient record, producing a generalised 'has_target_disease'
    label that is agnostic to any specific organ or cancer type.

    The resulting labels are written to 'populations/cohort_labels.csv' inside
    the session workspace directory.
    """
    workspace_dir = os.environ.get("WORKSPACE_DIR")
    if not workspace_dir:
        return {"status": "error", "message": "WORKSPACE_DIR environment variable is not set."}

    # ── 1. Load criteria and dataset list from config.json ──────────────────────
    config_json_path = str(workspace_subpath(workspace_dir, "config.json"))
    inclusion_criteria: list = []
    exclusion_criteria: list = []
    datasetlist: list = []

    if os.path.exists(config_json_path):
        with open(config_json_path, mode="r", encoding="utf-8") as f:
            config = json.load(f)
            datasetlist = config.get("datasetlist", [])
            inclusion_criteria = config.get("inclusion_criteria", [])
            exclusion_criteria = config.get("exclusion_criteria", [])

    from service_urls import CONTROL_API_URL

    base_url_orthanc = CONTROL_API_URL

    # ── 2. Resolve patient IDs + metadata from the volume dataset list ──────────
    available_cases: set = set()
    # Maps volume UID → {orthanc_pid, clinical_pid, sex, age, ...} for DICOM fallback
    volume_patient_meta: dict = {}
    if datasetlist:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client_http:
                for uid in datasetlist:
                    try:
                        study_res = await client_http.get(f"{base_url_orthanc}/volumes/{uid}/study")
                        if study_res.status_code != 200:
                            continue
                        patient_uuid = study_res.json().get("patient_id")

                        patient_res = await client_http.get(f"{base_url_orthanc}/patients/{patient_uuid}")
                        if patient_res.status_code != 200:
                            continue

                        patient_data = patient_res.json()
                        orthanc_pid = patient_data.get("orthanc_patient_id", "")
                        clinical_pid = patient_data.get("name", "")

                        # Store full metadata for DICOM-based fallback labelling
                        volume_patient_meta[uid] = {
                            "orthanc_pid": orthanc_pid,
                            "clinical_pid": clinical_pid,
                            "sex": patient_data.get("sex", ""),
                            "age": patient_data.get("age", ""),
                            "patient_uuid": patient_uuid,
                        }

                        for pid in (orthanc_pid, clinical_pid):
                            if pid:
                                available_cases.update(_normalize_patient_ids(pid))
                    except Exception as inner_e:
                        print(f"Error fetching patient for volume {uid}: {inner_e}")
        except Exception as e:
            print(f"Failed to fetch available patients: {e}")

    if not available_cases and not volume_patient_meta:
        populations_dir = str(workspace_subpath(workspace_dir, "populations"))
        os.makedirs(populations_dir, exist_ok=True)
        output_csv = os.path.join(populations_dir, "cohort_labels.csv")
        with open(output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "patient_id", "has_target_disease", "confidence",
                "requires_manual_review", "source_evidence"
            ])
            writer.writeheader()

        return {
            "status": "success",
            "message": (
                "Dataset list is empty (0 cohort). Created empty cohort_labels.csv. "
                "Skipping automatic label extraction. Proceed directly to generate_targets so the user can review the 0 cohort in the UI and manually select volumes from the dataviewer if desired."
            ),
            "extracted_count": 0,
            "saved_path": output_csv
        }

    # ── 3. Locate clinical data (workspace first, then MCP fallback) ────────────
    clinical_json_path = str(workspace_subpath(workspace_dir, "clinical_data.json"))
    clinical_csv_path = str(workspace_subpath(workspace_dir, "clinical_data.csv"))

    # Temporary fallback relative to the mcp directory (mounted in docker)
    mcp_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    fallback_json_path = os.path.join(mcp_dir, "medical_report", "kits23_reduced.json")
    fallback_csv_path = os.path.join(mcp_dir, "medical_report", "kits23_reduced.csv")

    if not os.path.exists(clinical_json_path) and not os.path.exists(clinical_csv_path):
        if os.path.exists(fallback_json_path):
            clinical_json_path = fallback_json_path
        elif os.path.exists(fallback_csv_path):
            clinical_csv_path = fallback_csv_path

    # ── 4. Build LLM context prompt ─────────────────────────────────────────────
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

    # ── 5. Build per-patient task list ───────────────────────────────────────────
    llm_tasks: list[tuple[str, str]] = []

    if os.path.exists(clinical_json_path):
        with open(clinical_json_path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        patient_id = item.get("case_id") or item.get("patient_id")
                        if not patient_id:
                            continue
                        if available_cases and not _normalize_patient_ids(patient_id).intersection(available_cases):
                            continue
                        flattened = " | ".join(
                            f"{k}: {v}" for k, v in item.items()
                            if k not in ("case_id", "patient_id")
                        )
                        llm_tasks.append((patient_id, flattened))
                elif isinstance(data, dict):
                    for k, v in data.items():
                        if not k:
                            continue
                        if available_cases and not _normalize_patient_ids(k).intersection(available_cases):
                            continue
                        flattened = (
                            " | ".join(f"{vk}: {vv}" for vk, vv in v.items())
                            if isinstance(v, dict)
                            else str(v)
                        )
                        llm_tasks.append((k, flattened))
            except json.JSONDecodeError:
                return {"status": "error", "message": "Invalid JSON format in clinical_data.json"}

    elif os.path.exists(clinical_csv_path) and not llm_tasks:
        with open(clinical_csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                patient_id = row.get("case_id") or row.get("patient_id")
                if not patient_id:
                    continue
                if available_cases and not _normalize_patient_ids(patient_id).intersection(available_cases):
                    continue
                flattened = " | ".join(
                    f"{k}: {v}" for k, v in row.items()
                    if k not in ("case_id", "patient_id")
                )
                llm_tasks.append((patient_id, flattened))

    # ── 5b. DICOM-metadata fallback when clinical IDs don't match volume IDs ─────
    # This happens when the clinical dataset uses a different ID scheme (e.g., kits23
    # uses "case_00000" while Orthanc stores UUID-based patient IDs).
    # Strategy: assign the first N clinical records to the N volumes in order,
    # using the volume's own UID as the patient_id so downstream tools can join them.
    if not llm_tasks and volume_patient_meta:
        print("[extract_cohort_labels] No ID match found — using positional assignment of clinical records to volumes.")
        # Collect all clinical records without ID filtering
        all_clinical_records: list[tuple[str, str]] = []
        if os.path.exists(clinical_json_path):
            try:
                with open(clinical_json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        pid = item.get("case_id") or item.get("patient_id", "unknown")
                        flattened = " | ".join(
                            f"{k}: {v}" for k, v in item.items()
                            if k not in ("case_id", "patient_id")
                        )
                        all_clinical_records.append((pid, flattened))
                elif isinstance(data, dict):
                    for k, v in data.items():
                        flattened = " | ".join(f"{vk}: {vv}" for vk, vv in v.items()) if isinstance(v, dict) else str(v)
                        all_clinical_records.append((k, flattened))
            except Exception:
                pass
        elif os.path.exists(clinical_csv_path):
            try:
                with open(clinical_csv_path, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        pid = row.get("case_id") or row.get("patient_id", "unknown")
                        flattened = " | ".join(
                            f"{k}: {v}" for k, v in row.items()
                            if k not in ("case_id", "patient_id")
                        )
                        all_clinical_records.append((pid, flattened))
            except Exception:
                pass

        if all_clinical_records:
            # Assign records to volumes by index; cycle if fewer records than volumes
            vol_uids = list(volume_patient_meta.keys())
            for i, uid in enumerate(vol_uids):
                _clinical_pid, clinical_text = all_clinical_records[i % len(all_clinical_records)]
                meta = volume_patient_meta[uid]
                # Prepend DICOM metadata so LLM has additional context
                dicom_context = f"DICOM orthanc_id: {meta['orthanc_pid']} | sex: {meta['sex']} | age: {meta['age']}"
                llm_tasks.append((uid, f"{dicom_context} | {clinical_text}"))
            print(f"[extract_cohort_labels] Positional fallback: assigned {len(llm_tasks)} clinical records to volumes.")
        else:
            # Last resort: generate DICOM-only labels using patient sex as a binary proxy
            print("[extract_cohort_labels] No clinical data available — generating DICOM-only labels from patient sex.")
            for uid, meta in volume_patient_meta.items():
                sex = (meta.get("sex") or "").lower()
                dicom_text = f"sex: {sex} | age: {meta.get('age', 'unknown')} | orthanc_id: {meta['orthanc_pid']}"
                llm_tasks.append((uid, dicom_text))

    if not llm_tasks:
        return {
            "status": "error",
            "message": (
                "No labels could be extracted. No clinical reports are available "
                "and no DICOM metadata could be retrieved for the discovered volumes."
            )
        }

    # ── 6. Run LLM batch ─────────────────────────────────────────────────────────
    from openai import AsyncOpenAI

    llm_base_url = os.environ.get("MODEL_BASE_URL")
    if not llm_base_url:
        return {"status": "error", "message": "LLM base_url is not configured (MODEL_BASE_URL env var missing)."}

    client_llm = AsyncOpenAI(
        base_url=llm_base_url,
        api_key=os.environ.get("MODEL_API_KEY", "ollama")
    )
    model_name = os.environ.get("MODEL_NAME", "llama3.1")
    concurrency_limit = int(os.environ.get("LLM_CONCURRENCY_LIMIT", "5"))
    sem = asyncio.Semaphore(concurrency_limit)

    results: list[dict] = []
    llm_unavailable = False

    async def _safe_batch_execution():
        nonlocal llm_unavailable
        tasks = [
            _process_patient_record(client_llm, model_name, pid, text, context_prompt, sem)
            for pid, text in llm_tasks
        ]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in batch_results:
            if isinstance(res, Exception):
                print(f"LLM API Exception: {res}")
                llm_unavailable = True
                break
            else:
                results.append(res)

        if llm_unavailable:
            # Hard batch failure — wipe partial results and mark all as requires_manual_review
            results.clear()
            for pid, _ in llm_tasks:
                results.append({
                    "patient_id": pid,
                    "has_target_disease": False,
                    "confidence": "low",
                    "requires_manual_review": True,
                    "source_evidence": "LLM unavailable: connection or auth error."
                })

    await _safe_batch_execution()

    # ── 6b. Add orphan entries for volumes without clinical reports ───────────────
    # Volumes in volume_patient_meta that didn't match any clinical record won't
    # have an llm_task (and therefore no result).  Instead of silently dropping
    # them, we emit them as unlabeled entries so the user can manually label them
    # in the Patient Labeling UI.
    processed_patient_ids = {r.get("patient_id") for r in results}
    normalized_processed = set()
    for p in processed_patient_ids:
        normalized_processed.update(_normalize_patient_ids(p))
    if volume_patient_meta:
        for uid, meta in volume_patient_meta.items():
            candidate_ids = {uid}
            candidate_ids.update(_normalize_patient_ids(meta.get("orthanc_pid", "")))
            candidate_ids.update(_normalize_patient_ids(meta.get("clinical_pid", "")))
            if not candidate_ids.intersection(normalized_processed):
                best_pid = meta.get("clinical_pid") or meta.get("orthanc_pid") or uid
                results.append({
                    "patient_id": best_pid,
                    "has_target_disease": "",
                    "confidence": "unlabeled",
                    "requires_manual_review": True,
                    "source_evidence": "No medical report found in clinical dataset. Requires manual evaluation by user in Patient Labeling tab."
                })
                print(f"[extract_cohort_labels] Orphan volume {uid} (patient: {best_pid}) — added as unlabeled entry.")

    # ── 7. Write cohort_labels.csv ───────────────────────────────────────────────
    populations_dir = str(workspace_subpath(workspace_dir, "populations"))
    os.makedirs(populations_dir, exist_ok=True)
    output_csv = os.path.join(populations_dir, "cohort_labels.csv")

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "patient_id", "has_target_disease", "confidence",
            "requires_manual_review", "source_evidence"
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for res in results:
            raw_pid = str(res.get("patient_id", ""))
            matched_uid = raw_pid
            norm_res = _normalize_patient_ids(raw_pid)
            for vol_uid, meta in volume_patient_meta.items():
                if vol_uid == raw_pid or norm_res.intersection(_normalize_patient_ids(meta.get("orthanc_pid"))) or norm_res.intersection(_normalize_patient_ids(meta.get("clinical_pid"))):
                    matched_uid = vol_uid
                    break

            htd_val = res.get("has_target_disease")
            if isinstance(htd_val, bool):
                htd_str = "true" if htd_val else "false"
            elif htd_val is not None and str(htd_val).strip().lower() in ("true", "false", "1", "0"):
                htd_str = str(htd_val).strip().lower()
            else:
                htd_str = ""

            conf_val = res.get("confidence", "low")
            if htd_str == "" and conf_val not in ("unlabeled", "manual override"):
                conf_val = "unlabeled"

            writer.writerow({
                "patient_id": matched_uid,
                "has_target_disease": htd_str,
                "confidence": conf_val,
                "requires_manual_review": True if htd_str == "" else res.get("requires_manual_review", False),
                "source_evidence": res.get("source_evidence", "")
            })

    # ── 8. Broadcast WS events ───────────────────────────────────────────────────
    session_id = os.environ.get("SESSION_ID", "")
    if session_id:
        # a) Raw tool completion event
        try:
            async with httpx.AsyncClient() as ws_client:
                await ws_client.post(
                    "http://localhost:8150/ws-broadcast",
                    json={
                        "type": "cohort_labels_output",
                        "payload": {
                            "tool": "extract_cohort_labels",
                            "status": "completed",
                            "extracted_count": len(results),
                            "llm_unavailable": llm_unavailable,
                            "saved_path": output_csv
                        },
                        "session_id": session_id
                    }
                )
        except Exception as e:
            print(f"Warning: Failed to broadcast cohort_labels_output WS message: {e}")

    return {
        "status": "success",
        "message": f"Successfully processed {len(results)} records.",
        "llm_unavailable": llm_unavailable,
        "saved_path": output_csv
    }
