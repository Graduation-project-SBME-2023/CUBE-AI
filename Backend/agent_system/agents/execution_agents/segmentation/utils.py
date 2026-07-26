import re
import gzip
import io
import os
import json
import shutil
import time
import asyncio
from pathlib import Path
import nrrd
import numpy as np
import tempfile
import requests
import httpx
import highdicom as hd
from typing import Dict, Any, Tuple
from pydicom.uid import generate_uid
import nibabel as nib
import logging
logger = logging.getLogger(__name__)
from PIL import Image

MONAI_URL = os.environ.get("MONAI_URL", "http://localhost:8000")
REQUEST_TIMEOUT_SECONDS = 10.0
KNOWN_SEGMENTATION_MODELS = {"voxtell", "vista", "segmentation_spleen", "nnunet"}
# ===============================
# MASK HELPERS
# ===============================

def extract_organ_label(segmentation_type: str) -> str:
    """
    Extracts the organ/label portion from a segmentation_type string.

    The stored format is:  '{organ label}-{model}-{timestamp}-...'
    e.g. 'liver lesion-voxtell-01:48:31 PM-g'
         'liver-vista-02:10:00 PM-g'

    Strategy: split on '-' and consume tokens that look like the organ name
    (i.e., stop at the first token that matches a known model name or looks
    like a time HH:MM, or is a single letter suffix).  If no separator is
    found return the whole string normalised.
    """
    if not segmentation_type:
        return ""

    parts = str(segmentation_type).split("-")
    organ_parts = []
    for part in parts:
        p = _normalize_model_name(part)
        # Stop if we hit a known model name or a timestamp token (HH:MM)
        if p in KNOWN_SEGMENTATION_MODELS or re.match(r'^\d{1,2}:\d{2}', part.strip()) or (len(p) == 1 and p.isalpha()):
            break
        organ_parts.append(part.strip())
    return normalize_label(" ".join(organ_parts)) if organ_parts else normalize_label(segmentation_type)


def matches_label(segmentation_type: str, label: str) -> bool:
    """
    Returns True only when *label* is a semantic match for the organ portion
    of *segmentation_type*, considering synonyms like 'splenic' == 'spleen'.

    The segmentation_type format is '{organ label}-{model}-{timestamp}-...'
    We extract just the organ part and compare it semantically to the target label.
    """
    from .medical_vocabulary import FLAT_MEDICAL_SYNONYMS
    
    def get_semantic_concepts(l: str) -> set:
        words = normalize_label(l).split()
        return set(FLAT_MEDICAL_SYNONYMS.get(w, w) for w in words)

    organ = extract_organ_label(segmentation_type)
    return get_semantic_concepts(organ) == get_semantic_concepts(label)


def _normalize_model_name(model_name: str) -> str:
    return normalize_label(model_name).replace(" ", "_")


def segmentation_model_matches(segmentation_type: str, model_name: str) -> bool:
    if not segmentation_type or not model_name:
        return False

    expected = _normalize_model_name(model_name)
    return any(_normalize_model_name(part) == expected for part in str(segmentation_type).split("-"))


def label_record_matches_model(label_record: dict, model_name: str) -> bool:
    if not model_name:
        return True

    for key in ("name", "model_name"):
        if _normalize_model_name(label_record.get(key, "")) == _normalize_model_name(model_name):
            return True

    return segmentation_model_matches(label_record.get("segmentation_type", ""), model_name)

async def exclude_existing_masks(volumes, labels, model_name=None):
    """
    Returns the subset of volumes that still need segmentation.

    A volume is EXCLUDED (i.e., skipped) only when it already has a mask that
    matches BOTH the target organ AND the selected model.  If only the organ
    matches but a different model was used, the volume stays eligible so the
    pipeline can produce a mask with the correct model.

    Parameters
    ----------
    volumes    : list of volume SeriesInstanceUIDs
    labels     : list of target organ labels, e.g. ["liver"]
    model_name : name of the selected automatic model, e.g. "vista".
                 When provided, a volume is only excluded if its existing mask
                 was produced by THIS model.  When None, any matching-organ mask
                 is sufficient to exclude the volume (original behaviour).
    """
    eligible = []

    for volume_uid in volumes:
        has_mask = False
        logger.info(f"Checking masks: volume={volume_uid}, labels={labels}")

        try:
            segmentations = await check_having_mask(volume_uid)

            if not segmentations:
                has_mask = False
            else:
                for seg in segmentations:
                    seg_type = seg.get("segmentation_type", "")
                    for label in labels:
                        organ_ok = matches_label(seg_type, label)
                        model_ok = label_record_matches_model(seg, model_name)
                        if organ_ok and model_ok:
                            has_mask = True
                            logger.info(
                                f"Match found: volume={volume_uid}, "
                                f"label={label}, model={model_name}, "
                                f"segmentation_type={seg_type}"
                            )
                            break
                    if has_mask:
                        break

        except Exception:
            logger.exception("Error checking mask for volume %s", volume_uid)
            has_mask = False

        if not has_mask:
            eligible.append(volume_uid)
            logger.info(f"Volume {volume_uid} added to eligible list")
        else:
            logger.info(f"Volume {volume_uid} excluded (matching mask exists for model='{model_name}', labels={labels})")

    return eligible

async def check_having_mask(volume_uid):
    """
    Checks if a segmentation mask exists for the given volume SeriesInstanceUID (volume_uid).
    Calls the endpoint /segmentations/by-volume/{volume_uid} and returns the list of segmentations.
    """
    url = f"{MONAI_URL}/segmentations/by-volume/{volume_uid}"
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.get(url)
    if response.status_code == 200:
        return response.json()
    logger.warning("GET %s returned %s", url, response.status_code)
    return []

def parse_multipart_response(headers, body):
    """
    Parses a multipart/mixed response that contains:
    - One part with Content-Type: application/json -> centroids dict
    - One part with Content-Type: application/octet-stream -> NRRD binary data
    Returns:
        (nrrd_bytes: bytes, centroids: dict)
    """
    content_type = headers.get('content-type', '')
    boundary_match = re.search(r'boundary=([^;]+)', content_type, re.IGNORECASE)
    if not boundary_match:
        raise ValueError("No boundary found in Content-Type header")

    boundary = boundary_match.group(1).strip()
    boundary_bytes = f"--{boundary}".encode('ascii')

    parts = split_multipart(body, boundary_bytes)

    json_data = None
    nrrd_bytes = None

    for part in parts:
        header_end = find_double_crlf(part)
        if header_end == -1:
            continue

        headers_part = part[:header_end].decode('ascii', errors='replace')
        body_part = part[header_end:]

        if 'application/json' in headers_part:
            try:
                json_text = body_part.decode('utf-8')
                json_data = json.loads(json_text)
            except Exception as e:
                raise ValueError(f"Failed to parse JSON part: {e}")

        elif 'application/octet-stream' in headers_part:
            nrrd_bytes = body_part

    if json_data is None or nrrd_bytes is None:
        raise ValueError(
            "Multipart response missing required parts: "
            "need application/json and application/octet-stream"
        )

    # Return in same order as original TS: data first, then centroids.
    return nrrd_bytes, json_data.get('centroids', {})

def split_multipart(buffer, boundary):
    """
    Splits the buffer into parts using the boundary.
    Skips the boundary line itself and trailing \r\n.
    """
    parts = []
    start = 0
    while start < len(buffer):
        boundary_index = find_subarray(buffer, boundary, start)
        if boundary_index == -1:
            break

        # start of actual part content = after boundary + \r\n
        part_start = boundary_index + len(boundary) + 2

        # check for closing boundary (ends with --)
        if buffer[part_start:part_start+2] == b'--':
            break

        next_boundary = find_subarray(buffer, boundary, part_start)
        part_end = next_boundary if next_boundary != -1 else len(buffer)

        # exclude trailing \r\n before next boundary
        part = buffer[part_start:part_end - 2]
        parts.append(part)
        start = part_end  

    return parts  

def find_subarray(haystack, needle, start=0):
    """
    Returns the starting index of the first occurrence of needle in haystack[start:],
    or -1 if not found.
    """
    n_h = len(haystack)
    n_n = len(needle)

    for i in range(start, n_h - n_n + 1):
        match = True
        for j in range(n_n):
            if haystack[i + j] != needle[j]:
                match = False
                break
        if match:
            return i

    return -1

def find_double_crlf(buffer):
    """      
    Finds the position right after the first \r\n\r\n occurrence.
    Returns -1 if not found.
    """
    for i in range(len(buffer) - 3):
        if (buffer[i]     == 13 and   # \r
            buffer[i + 1] == 10 and   # \n
            buffer[i + 2] == 13 and   # \r
            buffer[i + 3] == 10):     # \n
            return i + 4              # position after second \r\n

    return -1


# ===============================
# NRRD PARSER
# ===============================

def parse_nrrd_data(nrrd_bytes):
    """
    Parses NRRD data (raw bytes) and returns:

    """
    try:  
        with tempfile.NamedTemporaryFile(delete=False, suffix=".nrrd") as tmp:
            tmp.write(nrrd_bytes)
            tmp_path = tmp.name


        try:
            data_array, header = nrrd.read(tmp_path)
        finally:
            os.remove(tmp_path)
        
        if data_array.dtype != np.uint16:
            logger.warning("NRRD data type is %s; converting to uint16", data_array.dtype)
            data_array = data_array.astype(np.uint16)

        return data_array, header

    except Exception as e:
        # If pynrrd fails, fall back to manual gzip handling.
        try:
            if nrrd_bytes.startswith(b'\x1f\x8b'):  # gzip magic number
                decompressed = gzip.decompress(nrrd_bytes)
                with tempfile.NamedTemporaryFile(delete=False, suffix=".nrrd") as tmp:
                    tmp.write(decompressed)
                    tmp_path = tmp.name
                try:
                    data_array, header = nrrd.read(tmp_path)
                finally:
                    os.remove(tmp_path)
                return data_array.astype(np.uint16), header
            else:
                raise
        except Exception as inner_e:
            raise ValueError(f"Failed to parse NRRD data: {inner_e}") from inner_e


# ===============================
# DICOM SEG CREATION
# ===============================

def rgb_to_dicom_cielab(rgb):
    """
    Convert RGB [0-1] to DICOM CIELab 
    """
    import numpy as np
    
    rgb = np.asarray(rgb)  # shape (3,)
    # Simple sRGB to XYZ (D65 white point)
    xyz = np.dot(rgb, np.array([
        [0.4124, 0.3576, 0.1805],
        [0.2126, 0.7152, 0.0722],
        [0.0193, 0.1192, 0.9505]
    ]).T)
    
    xyz /= np.array([0.95047, 1.0, 1.08883])  # D65 normalize
    
    # XYZ to Lab
    xyz = np.where(xyz > 0.008856, xyz ** (1/3), (7.787 * xyz) + (16/116))
    L = (116 * xyz[1]) - 16
    a = 500 * (xyz[0] - xyz[1])
    b = 200 * (xyz[1] - xyz[2])
    
    # DICOM scales Lab to 0-255 range (approx)
    L = np.clip((L + 16) * (255 / 116), 0, 255)
    a = np.clip((a + 128) * (255 / 256), 0, 255)
    b = np.clip((b + 128) * (255 / 256), 0, 255)
    
    return [int(round(v)) for v in [L, a, b]]

def ensure_required_patient_tags(datasets):

    new_for_uid = generate_uid()
    for ds in datasets:
        # ds.FrameOfReferenceUID = new_for_uid # generate it to avoid conflicts
        ds.setdefault("PatientName", "Anonymous")
        ds.setdefault("PatientID", "Unknown")
        ds.setdefault("PatientBirthDate", "")
        ds.setdefault("PatientSex", "")
        ds.setdefault("FrameOfReferenceUID", "")
        ds.setdefault("StudyInstanceUID", ds.StudyInstanceUID)
        ds.setdefault("StudyID", "")
        ds.setdefault("StudyDate", "")
        ds.setdefault("StudyTime", "")
        ds.setdefault("AccessionNumber", "")
        ds.setdefault("ReferringPhysicianName", "")
        ds.setdefault("SeriesInstanceUID", ds.SeriesInstanceUID)
        ds.setdefault("Modality", ds.get("Modality", "OT"))

#  ================================
#  Download Data
#  ================================
def _get_vista_class_index(target_label: str) -> int | None:
    """
    Returns the integer class index for a target organ label in the VISTA
    model's multi-class NIfTI output, or None if not found.

    VISTA encodes each organ as a unique positive integer in the output
    NIfTI file (e.g. liver=5, gallbladder=4). We look this up from the
    VISTA params_schema mapping registered in the DB.
    """
    try:
        resp = requests.get(f"{MONAI_URL}/models/", timeout=5)
        if resp.status_code != 200:
            return None
        for m in resp.json():
            if m.get("name", "").lower() != "vista":
                continue
            mapping = (
                (m.get("params_schema") or {})
                .get("Class", {})
                .get("mapping", {})
            )
            # Try exact, then normalized variants
            norm = normalize_label(target_label)          # e.g. "liver"
            norm_under = norm.replace(" ", "_")           # e.g. "liver"
            for key, idx in mapping.items():
                if normalize_label(key) in (norm, norm_under):
                    return int(idx)
    except Exception as e:
        logger.warning("Could not fetch VISTA class index for '%s': %s", target_label, e)
    return None


async def download_and_pair_volumes(
    workspace_dir,
    datasetlist,
    chosen_path,
    selected_auto_model_for_volume,
    download_volumes=True,
    target_label=None,
):
    VOLUMES = datasetlist
    LABELS_BY_VOLUME_URL = f"{MONAI_URL}/segmentations/by-volume/"
    DOWNLOAD_IMAGES_URL  = f"{MONAI_URL}/datastore/images"
    DOWNLOAD_LABELS_URL  = f"{MONAI_URL}/datastore/labels"
    TASK_NAME  = f"{selected_auto_model_for_volume}_segmentation_task" if selected_auto_model_for_volume else "segmentation_task"

    # where MONAI actually saves files after the POST trigger
    MONAI_BASE_DIR = Path("/app/Backend/apps")
    MONAI_IMG_DIR   = MONAI_BASE_DIR / "radiology" / "General_Data"
    MONAI_LABEL_DIR = MONAI_BASE_DIR / "radiology" / "labels"

    FINAL_DIR_IMG   = os.path.join(workspace_dir, "volumes")
    FINAL_DIR_LABEL = os.path.join(workspace_dir, "masks")

    for d in [FINAL_DIR_IMG, FINAL_DIR_LABEL]:
        os.makedirs(d, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Helper functions remain exactly the same
    # ------------------------------------------------------------------ #
    def fetch_labels_for_volume(volume_uid):
        url = LABELS_BY_VOLUME_URL + volume_uid
        try:
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                logger.warning(f"GET {url} returned {r.status_code}")
                return []
            return r.json()
        except Exception:
            logger.exception("Error fetching labels for %s", volume_uid)
            return []

    def pick_latest_or_auto(labels, chosen_path="interactive", selected_auto_model=None, target_label=None):
        if not labels:
            return None

        # --- Automatic path ---
        if chosen_path == "automatic" and selected_auto_model:
            if not target_label:
                logger.warning(
                    "Automatic mask selection requires target_label when model='%s'; skipping.",
                    selected_auto_model,
                )
                return None

            # Mask selection is intentionally strict: model and organ must both match.
            for label in labels:
                seg_type = label.get("segmentation_type", "")
                model_matches = label_record_matches_model(label, selected_auto_model)
                organ_matches = matches_label(seg_type, target_label)
                if model_matches and organ_matches:
                    logger.info(
                        "Selected exact mask: model='%s', organ='%s', segmentation_type='%s', UID=%s",
                        selected_auto_model,
                        target_label,
                        seg_type,
                        label.get("label_series_instance_uid"),
                    )
                    return label
                elif model_matches and not organ_matches:
                    logger.warning(
                        "Model '%s' matched but organ did not: segmentation_type='%s', expected='%s'; skipping.",
                        selected_auto_model,
                        seg_type,
                        target_label,
                    )

            logger.warning(
                "No exact mask matched model='%s' and organ='%s'; skipping this volume.",
                selected_auto_model,
                target_label,
            )
            return None

        # --- Interactive / VoxTell path ---
        # Interactive labels may not always preserve model metadata, so prefer
        # model+organ and then allow organ-only. Never fall back across organs.
        if chosen_path == "interactive":
            if target_label:
                for label in labels:
                    seg_type = label.get("segmentation_type", "")
                    if selected_auto_model and label_record_matches_model(label, selected_auto_model) and matches_label(seg_type, target_label):
                        logger.info(
                            "Interactive exact model+organ match: model='%s', organ='%s', UID=%s",
                            selected_auto_model,
                            target_label,
                            label.get("label_series_instance_uid"),
                        )
                        return label

                for label in labels:
                    seg_type = label.get("segmentation_type", "")
                    if matches_label(seg_type, target_label):
                        logger.info(
                            "Interactive organ-only match: organ='%s', segmentation_type='%s', UID=%s",
                            target_label,
                            seg_type,
                            label.get("label_series_instance_uid"),
                        )
                        return label

                logger.warning(
                    "Interactive path found no label matching organ='%s'; skipping this volume.",
                    target_label,
                )
                return None

            if selected_auto_model:
                for label in labels:
                    if label_record_matches_model(label, selected_auto_model):
                        logger.info(
                            "Interactive model-only match for '%s' without target_label: UID=%s",
                            selected_auto_model,
                            label.get("label_series_instance_uid"),
                        )
                        return label
                logger.warning(
                    "Interactive path had model='%s' but no target_label and no model match; skipping.",
                    selected_auto_model,
                )
                return None

            logger.warning(
                "Interactive path has no model or target_label; skipping instead of selecting the newest label."
            )
            return None

        logger.warning(
            "Unknown segmentation path '%s'; skipping instead of selecting the newest label.",
            chosen_path,
        )
        return None

    def trigger_download(url, task_name, uids_list):
        if not uids_list:
            return False
        logger.info(f"POST {url} | task={task_name} | uids={uids_list}")
        try:
            r = requests.post(url, params={"task_name": task_name}, json=uids_list, timeout=60)
            logger.info("Trigger response: %s", r.status_code)
            if r.status_code != 200:
                logger.error("Trigger failed: %s", r.text[:300])
                return False
            return True
        except Exception:
            logger.exception("Trigger exception")
            return False

    async def wait_and_find(search_dir, uid_to_search_key, timeout_s=120, poll_interval=3.0):
        """
        Waits for NIfTI files to appear in *search_dir*.

        Parameters
        ----------
        uid_to_search_key : dict | list
            * dict  -> maps an arbitrary key (e.g. label_uid) to the substring
                      that should appear in the filename (e.g. volume_uid).
                      `found` is keyed by the *dict key*.
            * list  -> each element is both the key and the search substring
                      (legacy behaviour).
        """
        # Normalise to dict form
        if isinstance(uid_to_search_key, list):
            uid_to_search_key = {u: u for u in uid_to_search_key}

        deadline       = time.time() + timeout_s
        found          = {}
        remaining_keys = set(uid_to_search_key.keys())

        logger.info(
            f"Waiting for {len(remaining_keys)} file(s) in {search_dir} "
            f"(timeout={timeout_s}s)"
        )

        while time.time() < deadline and remaining_keys:
            if not os.path.isdir(search_dir):
                logger.warning(f"Directory does not exist yet: {search_dir}")
                await asyncio.sleep(poll_interval)
                continue

            for root, _, files in os.walk(search_dir):
                for fname in files:
                    if not fname.lower().endswith(".nii.gz"):
                        continue
                    for key in list(remaining_keys):
                        search_substr = uid_to_search_key[key]
                        if search_substr in fname:
                            full_path = os.path.join(root, fname)
                            found[key] = full_path
                            remaining_keys.discard(key)
                            logger.info("Found [%s] -> %s", key, full_path)

            if remaining_keys:
                elapsed = timeout_s - (deadline - time.time())
                logger.debug(
                    f"Found {len(found)}/{len(uid_to_search_key)}; "
                    f"waiting... ({elapsed:.0f}s elapsed)"
                )
                await asyncio.sleep(poll_interval)

        if remaining_keys:
            missing_info = {k: uid_to_search_key[k] for k in remaining_keys}
            logger.warning("Timeout; missing keys/search-substrings: %s", missing_info)
        return found

    def copy_volume(src_path, volume_uid, dest_dir):
        dst = os.path.join(dest_dir, f"{volume_uid}.nii.gz")
        shutil.copy2(src_path, dst)
        size_mb = os.path.getsize(dst) / 1_048_576
        logger.info("Copied volume -> %s (%.2f MB)", dst, size_mb)
        return dst

    def copy_and_normalize_label(src_path, volume_uid, dest_dir, organ_class_index=None):
        """
        Normalize a segmentation label NIfTI and save it as a binary {0,1} mask.

        Handles three formats:
          1. Binary {0, 255}  – simple threshold to {0,1}.
          2. Binary {0, 1}    – already correct, save as-is.
          3. Multi-class      – VISTA-style integer-per-organ output.
                               If *organ_class_index* is given, extract only
                               that organ's voxels (e.g. liver=5).  Without
                               an index we fall back to a generic >0 mask and
                               log a warning so the problem is visible.
        """
        dst = os.path.join(dest_dir, f"{volume_uid}.nii.gz")
        try:
            nii  = nib.load(src_path)
            
            # --- VOLUME SIZE LOGGING ---
            total_voxels = np.prod(nii.shape)
            estimated_ram_bytes = total_voxels * 8
            estimated_ram_gb = estimated_ram_bytes / (1024**3)
            logger.info(
                "Label %s estimated RAM: %.2f GB (no limit enforced).",
                volume_uid,
                estimated_ram_gb,
            )
            # ---------------------------

            data = nii.get_fdata()
            unique_vals = np.unique(data)
            logger.info(f"Label unique values before normalize: {unique_vals}")

            max_val = float(np.max(data))

            if max_val == 255.0:
                # Binary DICOM-style mask: {0, 255} → {0, 1}
                data = (data > 0).astype(np.uint8)
                logger.info("Converted {0,255} -> {0,1}")

            elif max_val <= 1.0:
                # Already binary {0, 1} — keep as uint8
                data = data.astype(np.uint8)
                logger.info("Label already binary {0,1}; keeping as-is.")

            else:
                # Multi-class label (e.g. VISTA: liver=5, gallbladder=4, ...)
                if organ_class_index is not None:
                    logger.info(
                        "Multi-class label detected (max=%.0f). "
                        "Extracting organ class index=%d for volume %s.",
                        max_val, organ_class_index, volume_uid,
                    )
                    data = (np.round(data).astype(np.int32) == organ_class_index).astype(np.uint8)
                else:
                    logger.warning(
                        "Multi-class label (max=%.0f) but no organ_class_index supplied for %s — "
                        "falling back to >0 binarization (may include wrong organs).",
                        max_val, volume_uid,
                    )
                    data = (data > 0).astype(np.uint8)

            nib.save(nib.Nifti1Image(data, nii.affine, nii.header), dst)
            size_mb = os.path.getsize(dst) / 1_048_576
            logger.info("Saved normalized label -> %s (%.2f MB)", dst, size_mb)
            return dst
        except Exception:
            logger.exception("Error normalizing label %s", src_path)
            return None

    # ------------------------------------------------------------------ #
    #  1. pair volumes and labels                                        #
    # ------------------------------------------------------------------ #
    volumes_to_process = []
    labels_to_process  = []

    logger.info(f"Processing {len(VOLUMES)} volumes...")
    for idx, vol_uid in enumerate(VOLUMES, 1):
        logger.info(f"[{idx}/{len(VOLUMES)}] Volume: {vol_uid}")
        labels = fetch_labels_for_volume(vol_uid)
        if not labels:
            logger.warning(f"No labels for {vol_uid}, skipping")
            continue
        # Pass the model name for BOTH automatic and interactive paths so that
        # pick_latest_or_auto can do an exact model-name match instead of
        # falling back to the latest timestamp (which could be a mask for a
        # different organ from a previous experiment).
        selected_model = selected_auto_model_for_volume
        latest = pick_latest_or_auto(labels, chosen_path=chosen_path, selected_auto_model=selected_model, target_label=target_label)
        
        if not latest:
            if chosen_path == "interactive":
                logger.info(f"No existing label found for {vol_uid}, but keeping volume for interactive manual segmentation.")
                volumes_to_process.append(vol_uid)
                labels_to_process.append(None)
            continue
            
        label_uid = latest.get("label_series_instance_uid")
        if not label_uid:
            if chosen_path == "interactive":
                logger.info(f"Empty label UID for {vol_uid}, but keeping volume for interactive manual segmentation.")
                volumes_to_process.append(vol_uid)
                labels_to_process.append(None)
            continue
            
        vol_uid_confirmed = latest.get("volume_series_instance_uid") or vol_uid
        logger.info(f"Paired: volume={vol_uid_confirmed}  label={label_uid}")
        volumes_to_process.append(vol_uid_confirmed)
        labels_to_process.append(label_uid)

    if not volumes_to_process:
        logger.error("Nothing to process; aborting.")
        return [], []

    logger.info(f"Pairs ready: {len(volumes_to_process)}")

    # ------------------------------------------------------------------ #
    #  2. trigger downloads (POST; MONAI saves to its local dirs)        #
    # ------------------------------------------------------------------ #
    if download_volumes:
        trigger_download(DOWNLOAD_IMAGES_URL, TASK_NAME, volumes_to_process)
    
    valid_labels = [uid for uid in labels_to_process if uid is not None]
    trigger_download(DOWNLOAD_LABELS_URL, TASK_NAME, valid_labels)

    # ------------------------------------------------------------------ #
    #  3. wait for files to appear in MONAI's local dirs                 #
    # ------------------------------------------------------------------ #
    # MONAI's /datastore/labels endpoint converts the DICOM SEG to NIfTI
    # and saves it as:  {label_series_instance_uid}.nii.gz
    # NOT by volume UID.  Pass labels_to_process as a plain list so that
    # wait_and_find searches for the label UID in the filename.

    found_volumes = {}
    if download_volumes:
        logger.info("Waiting for images to appear...")
        found_volumes = await wait_and_find(MONAI_IMG_DIR, volumes_to_process, timeout_s=600)

    logger.info("Waiting for labels to appear...")
    # Diagnostic: log what's already in MONAI_LABEL_DIR before waiting
    try:
        if os.path.isdir(MONAI_LABEL_DIR):
            existing = []
            for root, _, files in os.walk(MONAI_LABEL_DIR):
                for f in files:
                    if f.lower().endswith(".nii.gz"):
                        existing.append(os.path.join(root, f))
            logger.info(
                f"MONAI_LABEL_DIR='{MONAI_LABEL_DIR}'; "
                f"{len(existing)} .nii.gz file(s) already present. "
                f"Sample: {existing[:5]}"
            )
        else:
            logger.warning(f"MONAI_LABEL_DIR does not exist yet: {MONAI_LABEL_DIR}")
    except Exception as _diag_err:
        logger.warning(f"Diagnostic scan failed: {_diag_err}")

    # Search by label_uid in filename (MONAI saves as {label_uid}.nii.gz).
    # found_labels is keyed by label_uid -> file path.
    found_labels = await wait_and_find(
        MONAI_LABEL_DIR,
        valid_labels,   # plain list: key == search substring == label_uid
        timeout_s=600
    )
    logger.info(
        f"Label files found: {len(found_labels)}/{len(valid_labels)}; "
        f"keys: {list(found_labels.keys())[:3]}..."
    )

    # ------------------------------------------------------------------ #
    #  4. copy to workspace                                              #
    # ------------------------------------------------------------------ #

    # Resolve the organ class index for multi-class models (e.g. VISTA).
    # This is done once, outside the per-volume loop.
    organ_class_index = None
    if selected_auto_model_for_volume and target_label:
        organ_class_index = _get_vista_class_index(target_label)
        if organ_class_index is not None:
            logger.info(
                "Resolved organ class index for model='%s', label='%s': %d",
                selected_auto_model_for_volume, target_label, organ_class_index,
            )
        else:
            logger.info(
                "No VISTA class index found for model='%s', label='%s' — "
                "will use standard binarization.",
                selected_auto_model_for_volume, target_label,
            )

    successful = []
    skipped_oom = []
    for vol_uid, label_uid in zip(volumes_to_process, labels_to_process):
        label_src = found_labels.get(label_uid) if label_uid else None
        if not label_src and label_uid:
            logger.error(
                f"Label file never appeared for volume={vol_uid} "
                f"(label_uid={label_uid}); skipping pair"
            )
            continue
        elif not label_src and not label_uid:
            if chosen_path == "interactive":
                logger.info(f"Interactive manual volume {vol_uid} has no label, proceeding with volume only.")
            else:
                logger.error(f"No label UID for volume={vol_uid} and path is not interactive; skipping pair")
                continue

        vol_result = True  # Default to True if we aren't downloading volumes
        if download_volumes:
            vol_src = found_volumes.get(vol_uid)
            if not vol_src:
                logger.error("Volume file never appeared for %s; skipping pair", vol_uid)
                continue
            vol_result = copy_volume(vol_src, vol_uid, FINAL_DIR_IMG)

        if label_src:
            label_result = copy_and_normalize_label(
                label_src, vol_uid, FINAL_DIR_LABEL,
                organ_class_index=organ_class_index,
            )
        else:
            label_result = True # No label to process for interactive manual

        if label_result == "SKIPPED_OOM":
            skipped_oom.append(vol_uid)
            continue

        if vol_result and label_result:
            successful.append(vol_uid)
        else:
            logger.error("Failed to copy vol/label for %s", vol_uid)

    logger.info("=" * 60)
    logger.info(f"DONE: {len(successful)}/{len(volumes_to_process)} pairs ready in workspace")
    logger.info("=" * 60)
    return successful, skipped_oom
#  ================================
#  Generate GIF Overlays for front view
#  ================================
def generate_gif_overlays(workspace_dir, uids, on_gif_ready=None):
    """
    Generates animated GIF overlays slicing through the Z-axis where the mask is present.
    Returns a list of dictionaries formatted for the React frontend.

    Args:
        workspace_dir: path to the session workspace.
        uids: list of SeriesInstanceUIDs to process.
        on_gif_ready: optional sync callable(volume_entry: dict) called immediately
                      after each GIF is saved, so callers can stream results
                      to the frontend one-by-one instead of waiting for the full batch.
    """
    volumes_dir = os.path.join(workspace_dir, "volumes")
    masks_dir = os.path.join(workspace_dir, "masks")
    output_dir = os.path.join(workspace_dir, "overlays")
    workspace_folder_name = os.path.basename(workspace_dir)
    os.makedirs(output_dir, exist_ok=True)
    generated_volumes = []

    for uid in uids:
        vol_path = os.path.join(volumes_dir, f"{uid}.nii.gz")
        mask_path = os.path.join(masks_dir, f"{uid}.nii.gz")
        gif_filename = f"{uid}.gif"
        gif_path = os.path.join(output_dir, gif_filename)

        if not os.path.exists(vol_path) or not os.path.exists(mask_path):
            logger.warning(f"Missing files for {uid}. Skipping GIF generation.")
            continue

        try:
            vol_data = nib.load(vol_path).get_fdata()
            mask_data = nib.load(mask_path).get_fdata()

            # Some models return masks for a subset of slices; cap Z to the common range.
            if vol_data.shape != mask_data.shape:
                logger.warning(
                    f"Shape mismatch for {uid}: vol={vol_data.shape}, mask={mask_data.shape}. "
                    f"Capping Z to min of both."
                )
            max_z = min(vol_data.shape[2], mask_data.shape[2])

            # safe zone
            z_indices = np.any(mask_data[:, :, :max_z] > 0, axis=(0, 1))
            active_slices = np.where(z_indices)[0]

            is_empty_mask = False
            if len(active_slices) == 0:
                logger.warning(f"Empty mask for {uid}. Generating volume preview GIF without mask overlay.")
                is_empty_mask = True
                start_z = max(0, max_z // 4)
                end_z = min(max_z - 1, (3 * max_z) // 4)
            else:
                # padding
                start_z = max(0, active_slices[0] - 2)
                end_z = min(max_z - 1, active_slices[-1] + 2)

            step = 2 if (end_z - start_z) < 100 else 4
            frames = []
            for z in range(start_z, end_z + 1, step):
                v_slice = vol_data[:, :, z]
                m_slice = mask_data[:, :, z]

                # normalize
                v_min, v_max = np.percentile(v_slice, (1, 99))
                if v_max - v_min == 0:
                    v_norm = np.zeros_like(v_slice, dtype=np.uint8)
                else:
                    v_norm = np.clip((v_slice - v_min) / (v_max - v_min) * 255, 0, 255).astype(np.uint8)

                # convert to rgb "cyan"
                rgb_frame = np.stack([v_norm] * 3, axis=-1)

                # overlay if mask is not empty
                if not is_empty_mask:
                    mask_idx = m_slice > 0
                    rgb_frame[mask_idx] = [90, 204, 230]

                # rotate 90
                rgb_frame = np.rot90(rgb_frame)

                frames.append(Image.fromarray(rgb_frame))

            # save GIF
            if frames:
                frames[0].save(
                    gif_path,
                    save_all=True,
                    append_images=frames[1:],
                    duration=100,
                    loop=0
                )

                volume_entry = {
                    "seriesInstanceUid": uid,
                    "gifUrl": f"http://localhost:8150/files/{workspace_folder_name}/overlays/{gif_filename}",
                    "isEmptyMask": is_empty_mask
                }
                if is_empty_mask:
                    volume_entry["warning"] = "Volume mask is empty"
                generated_volumes.append(volume_entry)
                logger.info(f"Generated GIF for {uid} (empty_mask={is_empty_mask})")

                # Notify caller immediately for progressive broadcast.
                if on_gif_ready is not None:
                    try:
                        on_gif_ready(volume_entry)
                    except Exception as cb_err:
                        logger.warning(f"on_gif_ready callback failed for {uid}: {cb_err}")

        except Exception:
            logger.exception("Failed to generate GIF for %s", uid)
        finally:
            # Force garbage collection to prevent CPU OOM
            if 'vol_data' in locals(): del vol_data
            if 'mask_data' in locals(): del mask_data
            if 'frames' in locals(): del frames
            import gc
            gc.collect()

    return generated_volumes

# =================================
#  Label normalisation
# =================================

def normalize_label(label: str) -> str:
    """Normalize an LLM-generated label to the space-separated form used by model label catalogs.
    e.g.  'lung_nodule' -> 'lung nodule',  'Pulmonary_Nodule' -> 'pulmonary nodule'
    """
    return re.sub(r"\s+", " ", str(label or "").replace("_", " ")).lower().strip()

# NOTE: VoxTell label availability is no longer checked here.
# VoxTell's embed_text_prompts() handles all three cases at inference time:
#   1. Cache hit  → use pre-computed embedding directly.
#   2. Fallback alias → use the nearest cached embedding.
#   3. Cache miss → generate a dynamic embedding on the fly and persist it.
# A static catalog check in the agent layer added no value and is removed.

# =================================
#  remove volumes and labels from working space that was discarded by user
# =================================
def cleanup_discarded(workspace_dir, accepted_uids, modified_uids):
    """
    Removes discarded volumes and masks.
    - Keeps volumes for BOTH accepted and modified UIDs.
    - Keeps masks ONLY for accepted UIDs (modified masks are removed to be re-downloaded).
    """
    volumes_dir = os.path.join(workspace_dir, "volumes")
    masks_dir = os.path.join(workspace_dir, "masks")
    
    # we want to keep the NIfTI volume files for both accepted and modified
    uids_to_keep_volumes = set(accepted_uids) | set(modified_uids)
    
    # cleanup volumes
    if os.path.exists(volumes_dir):
        for fname in os.listdir(volumes_dir):
            if fname.lower().endswith(".nii.gz"):
                uid = fname.replace(".nii.gz", "")
                if uid not in uids_to_keep_volumes:
                    os.remove(os.path.join(volumes_dir, fname))
                    logger.info(f"Removed discarded volume: {fname}")

    # cleanup masks (labels) — keep for both accepted AND modified
    uids_to_keep_masks = set(accepted_uids) | set(modified_uids)
    if os.path.exists(masks_dir):
        for fname in os.listdir(masks_dir):
            if fname.lower().endswith(".nii.gz"):
                uid = fname.replace(".nii.gz", "")
                if uid not in uids_to_keep_masks:
                    os.remove(os.path.join(masks_dir, fname))
                    logger.info(f"Removed mask (discarded): {fname}")
