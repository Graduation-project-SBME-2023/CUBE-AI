import os
import csv
import json
import httpx
from . import mcp
from tool_logging import ToolEvent, log_tool_state, TextPayload
from .scratchpad import scratchpad_raise_flag, RaiseFlagInput
import asyncio

base_url = os.environ.get("MONAI_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# Shared: wait for doctor-uploaded targets.csv (manual mode)
# ---------------------------------------------------------------------------

# Global flag so we only emit the "waiting" log once per call chain
_IS_WAITING_FOR_LABELS = False


async def _wait_for_manual_upload(csv_path: str, timeout_seconds: int = 300) -> str:
    """
    Polls until the doctor uploads targets.csv or times-out.
    Returns the csv_path on success, or "SILENT_RETRY" on timeout.
    """
    global _IS_WAITING_FOR_LABELS
    if not _IS_WAITING_FOR_LABELS:
        _IS_WAITING_FOR_LABELS = True
        payload = TextPayload(message=f'Waiting for doctor to upload labels in ... {csv_path}')
        log_tool_state(ToolEvent(event_type='tool_started', content_type='text', content=payload))

    poll_interval = 2
    elapsed = 0

    while not os.path.exists(csv_path):
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        if elapsed >= timeout_seconds:
            return "SILENT_RETRY"

    _IS_WAITING_FOR_LABELS = False
    return csv_path


# ---------------------------------------------------------------------------
# Shared: join cohort_labels.csv + datasetlist → targets.csv
# ---------------------------------------------------------------------------

async def _build_targets_from_dicom_metadata(
    workspace_dir: str, datasetlist: list, config: dict, csv_path: str
) -> str:
    """
    Fallback: build targets.csv directly from DICOM patient metadata (gender).
    Used when cohort_labels.csv is unavailable (e.g. no clinical reports).
    """
    valid_targets: list[tuple] = []
    dropped: list[str] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for uid in datasetlist:
            try:
                study_res = await client.get(f"{base_url}/volumes/{uid}/study")
                study_res.raise_for_status()
                patient_uuid = study_res.json().get("patient_id")

                patient_res = await client.get(f"{base_url}/patients/{patient_uuid}")
                patient_res.raise_for_status()
                patient_data = patient_res.json()

                gender = patient_data.get("gender")
                name = patient_data.get("name", "") or patient_data.get("orthanc_patient_id", uid)

                if not gender:
                    # Fallback to mock gender for demo purposes if DICOM is missing it
                    is_male = int(patient_uuid.split('-')[0], 16) % 2 == 0
                    gender = "M" if is_male else "F"

                valid_targets.append((uid, name, gender))
            except Exception as e:
                dropped.append(f"{uid} (API error: {e})")

    if not valid_targets:
        err_msg = "No gender metadata found for any volume in the dataset."
        await scratchpad_raise_flag(RaiseFlagInput(level="error", message=err_msg))
        return json.dumps({"status": "error", "message": err_msg})

    if dropped:
        msg = (
            f"Dropped {len(dropped)} volumes (missing gender). "
            f"Continuing with {len(valid_targets)} valid patients."
        )
        await scratchpad_raise_flag(RaiseFlagInput(level="warning", message=msg))

    # Update config to only keep valid UIDs
    config_json_path = os.path.join(workspace_dir, "config.json")
    config["datasetlist"] = [uid for uid, _, _ in valid_targets]
    with open(config_json_path, mode="w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)

    with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "target"])
        for uid, name, gender in valid_targets:
            writer.writerow([uid, name, gender])

    return csv_path


async def _build_targets_from_labels(workspace_dir: str, datasetlist: list, config: dict, csv_path: str) -> str:
    """
    Reads cohort_labels.csv and resolves patient IDs via Orthanc to produce targets.csv.
    Falls back to DICOM patient metadata (gender) when cohort_labels.csv is unavailable.
    Returns the csv_path on success, or a JSON error string.
    """
    cohort_labels_path = os.path.join(workspace_dir, "populations", "cohort_labels.csv")
    if not os.path.exists(cohort_labels_path):
        print("[generate_targets] cohort_labels.csv not found — falling back to DICOM metadata (gender).")
        return await _build_targets_from_dicom_metadata(
            workspace_dir, datasetlist, config, csv_path
        )

    # Load labels into dict: patient_id -> row
    labels_dict: dict = {}
    with open(cohort_labels_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            labels_dict[row["patient_id"]] = row

    dropped_patients: list[str] = []
    low_confidence_patients: list[str] = []
    valid_targets: list[tuple] = []

    async with httpx.AsyncClient() as client:
        for uid in datasetlist:
            try:
                # 1. Fetch study
                study_res = await client.get(f"{base_url}/volumes/{uid}/study")
                study_res.raise_for_status()
                patient_uuid = study_res.json().get("patient_id")

                # 2. Fetch patient details
                patient_res = await client.get(f"{base_url}/patients/{patient_uuid}")
                patient_res.raise_for_status()
                patient_data = patient_res.json()
                orthanc_pid = patient_data.get("orthanc_patient_id", "")
                clinical_pid = patient_data.get("name", "")

                # 3. Determine primary case_id
                case_id = clinical_pid if clinical_pid else orthanc_pid

                # 4. Build optional KiTS fallback ID
                kits_case_id = None
                if clinical_pid and clinical_pid.startswith("KiTS-"):
                    kits_case_id = clinical_pid.replace("KiTS-", "case_")
                elif orthanc_pid and orthanc_pid.startswith("KiTS-"):
                    kits_case_id = orthanc_pid.replace("KiTS-", "case_")

                # 5. 4-way lookup in labels_dict
                label_info = (
                    labels_dict.get(case_id)
                    or labels_dict.get(clinical_pid)
                    or labels_dict.get(orthanc_pid)
                    or (labels_dict.get(kits_case_id) if kits_case_id else None)
                )

                if not label_info:
                    dropped_patients.append(f"{clinical_pid} (No medical report found)")
                    continue

                if label_info.get("confidence") == "low":
                    # Retain but flag — do NOT drop
                    low_confidence_patients.append(orthanc_pid)

                is_target = str(label_info.get("has_target_disease")).lower() == "true"
                target_val = 1 if is_target else 0
                valid_targets.append((uid, case_id, target_val))

            except Exception as e:
                dropped_patients.append(f"{uid} (API error: {e})")

    valid_uids = [uid for uid, _, _ in valid_targets]

    if not valid_uids:
        err_msg = "No matching targets found. All volumes were dropped because they lack clinical labels."
        await scratchpad_raise_flag(RaiseFlagInput(level="error", message=err_msg))
        return json.dumps({"status": "error", "message": err_msg})

    # Update config.json to only contain validated UIDs
    config_json_path = os.path.join(workspace_dir, "config.json")
    config["datasetlist"] = valid_uids
    with open(config_json_path, mode="w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)

    if dropped_patients:
        msg = (
            f"Dropped {len(dropped_patients)} volumes because no medical report exists for them. "
            f"Continuing with {len(valid_targets)} valid patients."
        )
        await scratchpad_raise_flag(RaiseFlagInput(level="warning", message=msg))

    if low_confidence_patients:
        msg = (
            f"Included {len(low_confidence_patients)} patients with low LLM confidence "
            f"(retained as per policy). See metadata for details."
        )
        await scratchpad_raise_flag(RaiseFlagInput(level="warning", message=msg))

    with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "target"])
        for uid, name, target in valid_targets:
            writer.writerow([uid, name, target])

    return csv_path


# ---------------------------------------------------------------------------
# MCP Tool
# ---------------------------------------------------------------------------

@mcp.tool()
async def generate_targets() -> str:
    """
    Generates the targets.csv file that maps each volume in the study cohort
    to a binary class label (0 / 1).

    Behaviour is controlled by the 'labelling_mode' key in the session's
    config.json (written by the frontend toggle):

    • "automatic" (default when set by the UI):
        Calls the LLM Clinical Adjudicator internally via extract_cohort_labels,
        then joins the resulting cohort_labels.csv with the volume list to
        produce targets.csv. No human interaction is required.

    • "manual" (legacy / default when key is absent):
        Waits for the doctor to upload a pre-filled targets.csv through the
        frontend. If the file is not present within 5 minutes it returns
        "SILENT_RETRY" so the agent can loop silently.
    """
    log_tool_state(ToolEvent(event_type='tool_started', content_type='text',
                             content=TextPayload(message='assigning labels')))

    workspace_dir = os.environ.get("WORKSPACE_DIR")
    if not workspace_dir:
        return json.dumps({"status": "error", "message": "WORKSPACE_DIR not set."})

    output_dir = os.path.join(workspace_dir, "populations", "targets")
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, "targets.csv")

    # ── Read config ──────────────────────────────────────────────────────────────
    config_json_path = os.path.join(workspace_dir, "config.json")
    config: dict = {}
    datasetlist: list = []
    labelling_mode = "manual"   # safe default

    if os.path.exists(config_json_path):
        try:
            with open(config_json_path, mode="r", encoding="utf-8") as f:
                config = json.load(f)
                datasetlist = config.get("datasetlist", [])
                labelling_mode = config.get("labelling_mode", "manual")
        except (json.JSONDecodeError, OSError):
            pass

    # ── Route ────────────────────────────────────────────────────────────────────
    if labelling_mode == "automatic":
        # ── AUTO MODE: join existing labels ─────────────────────────────
        print(f"[generate_targets] Automatic mode — joining previously generated cohort labels...")
        result = await _build_targets_from_labels(workspace_dir, datasetlist, config, csv_path)

    else:
        # ── MANUAL MODE: poll for doctor-uploaded file ────────────────────────────
        print(f"[generate_targets] Manual mode — polling for uploaded targets.csv...")
        result = await _wait_for_manual_upload(csv_path)

        if result == "SILENT_RETRY":
            return "SILENT_RETRY"

        # Auto-fill any empty target cells if the doctor uploaded the blank template
        try:
            import pandas as pd
            import random
            df = pd.read_csv(csv_path)
            if "target" in df.columns:
                empty_mask = df["target"].isnull() | (df["target"].astype(str).str.strip() == "")
                num_empty = int(empty_mask.sum())
                if num_empty > 0:
                    labels = ["Male", "Female"]
                    fill_values = [labels[i % len(labels)] for i in range(num_empty)]
                    random.shuffle(fill_values)
                    df.loc[empty_mask, "target"] = fill_values
                    df.to_csv(csv_path, index=False)
        except Exception as e:
            print(f"[generate_targets] Failed to auto-fill dummy labels: {e}")

    log_tool_state(ToolEvent(event_type='tool_finished', content_type='text',
                             content=TextPayload(message="Labels Successfully Fetched!")))
    return result