import os
import csv
import json
import httpx
import logging
from . import mcp
from tool_logging import ToolEvent, log_tool_state, TextPayload
from .scratchpad import scratchpad_raise_flag, RaiseFlagInput
import asyncio

base_url = os.environ.get("MONAI_URL", "http://localhost:8000")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared: wait for doctor-uploaded targets.csv (manual mode)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Shared: wait for explicit human cohort approval (HITL Gate)
# ---------------------------------------------------------------------------

_IS_WAITING_FOR_APPROVAL = False


async def _wait_for_cohort_approval(workspace_dir: str, config_path: str, csv_path: str, timeout_seconds: int = 120) -> str:
    """
    Polls until config.json contains 'cohort_approved': True (set when the user clicks
    'Approve & Lock Cohort' on Subpage 4 of the Population UI).
    Returns csv_path on success, or 'SILENT_RETRY' on timeout.
    """
    global _IS_WAITING_FOR_APPROVAL

    def is_approved() -> bool:
        if os.path.exists(config_path):
            try:
                with open(config_path, mode="r", encoding="utf-8") as f:
                    c = json.load(f)
                    return c.get("cohort_approved", False) is True
            except Exception:
                return False
        return False

    if is_approved():
        _IS_WAITING_FOR_APPROVAL = False
        return csv_path

    if not _IS_WAITING_FOR_APPROVAL:
        _IS_WAITING_FOR_APPROVAL = True
        payload = TextPayload(message="Waiting for doctor to review and approve final cohort in UI...")
        log_tool_state(ToolEvent(event_type='tool_started', content_type='text', content=payload))
        session_id = os.environ.get("SESSION_ID")
        if session_id:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(
                        f'{os.environ.get("MAS_BASE_URL", "http://localhost:8150").rstrip("/")}/ws-broadcast',
                        json={
                            "type": "agent_waiting_for_user_input",
                            "payload": {
                                "agent": "study_population_planner_agent",
                                "status": "WAITING_FOR_REVIEW",
                            },
                            "session_id": session_id,
                        },
                    )
            except Exception as exc:
                logger.warning("Could not publish cohort-approval waiting state: %s", exc)

    poll_interval = 2
    elapsed = 0

    while not is_approved():
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        if elapsed >= timeout_seconds:
            return "SILENT_RETRY"

    _IS_WAITING_FOR_APPROVAL = False
    return csv_path


# ---------------------------------------------------------------------------
# Shared: join cohort_labels.csv + datasetlist → draft targets.csv
# ---------------------------------------------------------------------------

async def _build_targets_from_labels(workspace_dir: str, datasetlist: list, config: dict, csv_path: str) -> str:
    """
    Reads cohort_labels.csv and resolves patient IDs via Orthanc to produce an initial targets.csv.
    If cohort_labels.csv is missing in automatic mode, runs extract_cohort_labels first.
    Does NOT modify datasetlist in config.json so that unlabeled/orphan volumes stay accessible in the UI.
    Returns csv_path on success, or a JSON error string.
    """
    from .population.cohort_labels import extract_cohort_labels

    cohort_labels_path = os.path.join(workspace_dir, "populations", "cohort_labels.csv")
    if not os.path.exists(cohort_labels_path):
        print("[generate_targets] cohort_labels.csv not found — running extract_cohort_labels automatically...")
        extraction_result = await extract_cohort_labels()
        if isinstance(extraction_result, dict) and extraction_result.get("status") == "error":
            err_msg = f"extract_cohort_labels failed: {extraction_result.get('message', 'unknown error')}"
            await scratchpad_raise_flag(RaiseFlagInput(level="error", message=err_msg))
            return json.dumps({"status": "error", "message": err_msg})
        if not os.path.exists(cohort_labels_path):
            err_msg = "cohort_labels.csv still missing after extract_cohort_labels ran."
            await scratchpad_raise_flag(RaiseFlagInput(level="error", message=err_msg))
            return json.dumps({"status": "error", "message": err_msg})

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
                study_res = await client.get(f"{base_url}/volumes/{uid}/study")
                study_res.raise_for_status()
                patient_uuid = study_res.json().get("patient_id")

                patient_res = await client.get(f"{base_url}/patients/{patient_uuid}")
                patient_res.raise_for_status()
                patient_data = patient_res.json()
                orthanc_pid = patient_data.get("orthanc_patient_id", "")
                clinical_pid = patient_data.get("name", "")

                case_id = clinical_pid if clinical_pid else orthanc_pid

                kits_case_id = None
                if clinical_pid and clinical_pid.startswith("KiTS-"):
                    kits_case_id = clinical_pid.replace("KiTS-", "case_")
                elif orthanc_pid and orthanc_pid.startswith("KiTS-"):
                    kits_case_id = orthanc_pid.replace("KiTS-", "case_")

                label_info = (
                    labels_dict.get(case_id)
                    or labels_dict.get(clinical_pid)
                    or labels_dict.get(orthanc_pid)
                    or (labels_dict.get(kits_case_id) if kits_case_id else None)
                    or labels_dict.get(uid)
                )

                if not label_info:
                    dropped_patients.append(f"{case_id} (No matching label row)")
                    continue

                if label_info.get("confidence") == "low":
                    low_confidence_patients.append(orthanc_pid)

                htd = str(label_info.get("has_target_disease", "")).strip().lower()
                if htd not in ("true", "false"):
                    # Unlabeled / pending user decision in UI
                    continue

                is_target = htd == "true"
                target_val = 1 if is_target else 0
                valid_targets.append((uid, case_id, target_val))

            except Exception as e:
                dropped_patients.append(f"{uid} (API error: {e})")

    if dropped_patients:
        msg = f"{len(dropped_patients)} volumes currently have no valid labels or medical reports. Awaiting user review in UI."
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
    Generates the targets.csv file mapping study cohort volumes to class labels (0/1)
    and acts as the Human-in-the-Loop approval gate before segmentation.

    In both 'automatic' and 'manual' labelling modes, after preparing initial candidate
    labels (or skeleton records), this tool waits for explicit human review and cohort
    approval via the interactive frontend UI. When unapproved, returns 'SILENT_RETRY'
    so the agent loops silently until the doctor signs off.
    """
    log_tool_state(ToolEvent(event_type='tool_started', content_type='text',
                             content=TextPayload(message='preparing targets & awaiting cohort approval')))

    workspace_dir = os.environ.get("WORKSPACE_DIR")
    if not workspace_dir:
        return json.dumps({"status": "error", "message": "WORKSPACE_DIR not set."})

    output_dir = os.path.join(workspace_dir, "populations", "targets")
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, "targets.csv")

    config_json_path = os.path.join(workspace_dir, "config.json")
    config: dict = {}
    datasetlist: list = []
    labelling_mode = "automatic"

    if os.path.exists(config_json_path):
        try:
            with open(config_json_path, mode="r", encoding="utf-8") as f:
                config = json.load(f)
                datasetlist = config.get("datasetlist", [])
                labelling_mode = config.get("labelling_mode", "automatic")
        except (json.JSONDecodeError, OSError):
            pass

    if not datasetlist:
        err_msg = (
            "generate_targets called before volume search: datasetlist is empty in config.json. "
            "You MUST call search_volumes_by_description first to populate the volume list, "
            "then call generate_targets."
        )
        await scratchpad_raise_flag(RaiseFlagInput(level="error", message=err_msg))
        return json.dumps({"status": "error", "message": err_msg})

    if labelling_mode == "automatic":
        print(f"[generate_targets] Automatic mode — building candidate targets for {len(datasetlist)} volumes...")
        res = await _build_targets_from_labels(workspace_dir, datasetlist, config, csv_path)
        if res.startswith("{") and "error" in res:
            return res
    else:
        print(f"[generate_targets] Manual mode — checking/initializing manual label workspace...")
        cohort_labels_path = os.path.join(workspace_dir, "populations", "cohort_labels.csv")
        if not os.path.exists(cohort_labels_path):
            os.makedirs(os.path.join(workspace_dir, "populations"), exist_ok=True)
            with open(cohort_labels_path, mode="w", newline="", encoding="utf-8") as f:
                fieldnames = ["patient_id", "has_target_disease", "confidence", "requires_manual_review", "source_evidence"]
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for uid in datasetlist:
                    writer.writerow({
                        "patient_id": uid,
                        "has_target_disease": "",
                        "confidence": "unlabeled",
                        "requires_manual_review": "True",
                        "source_evidence": "Manual mode — awaiting user selection in UI"
                    })
        # Build initial targets (likely empty until user saves labels in UI)
        await _build_targets_from_labels(workspace_dir, datasetlist, config, csv_path)

    # HITL Approval Gate: wait for doctor to sign off on Subpage 4
    result = await _wait_for_cohort_approval(workspace_dir, config_json_path, csv_path)

    if result != "SILENT_RETRY":
        log_tool_state(ToolEvent(event_type='tool_finished', content_type='text',
                                 content=TextPayload(message="Cohort Approved & Targets Locked!")))
    return result

