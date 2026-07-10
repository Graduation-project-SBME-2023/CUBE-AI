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
import highdicom as hd
from typing import Dict, Any, Tuple
from pydicom.uid import generate_uid
import nibabel as nib
import logging
logger = logging.getLogger(__name__)
from PIL import Image
import csv
import pandas as pd

MONAI_URL = os.environ.get("MONAI_URL", "http://localhost:8000")
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
    KNOWN_MODELS = {"voxtell", "vista", "segmentation_spleen", "nnunet"}
    parts = segmentation_type.split("-")
    organ_parts = []
    for part in parts:
        p = part.strip().lower()
        # Stop if we hit a known model name or a timestamp token (HH:MM)
        if p in KNOWN_MODELS or re.match(r'^\d{1,2}:\d{2}$', p) or (len(p) == 1 and p.isalpha()):
            break
        organ_parts.append(part.strip())
    return " ".join(organ_parts).lower() if organ_parts else segmentation_type.lower()


def matches_label(segmentation_type: str, label: str) -> bool:
    """
    Returns True only when *label* is an **exact** match for the organ portion
    of *segmentation_type*.

    The segmentation_type format is '{organ label}-{model}-{timestamp}-...'
    We extract just the organ part and compare it directly to the target label.

    Examples
    --------
    matches_label("liver lesion-voxtell-01:48:31 PM-g", "liver")        → False
    matches_label("liver-voxtell-01:48:31 PM-g",        "liver")        → True
    matches_label("liver lesion-voxtell-01:48:31 PM-g", "liver lesion") → True
    matches_label("liver-vista-02:10:00 PM-g",          "liver")        → True
    """
    organ = extract_organ_label(segmentation_type)
    return organ == label.lower().strip()

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
                        organ_ok  = matches_label(seg_type, label)
                        # If a model name is given, also require it to appear in seg_type.
                        model_ok  = (model_name is None) or (model_name in seg_type)
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

        except Exception as e:
            logger.error(f"Error checking mask: {e}", exc_info=True)
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
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    else:
        return []

def parse_multipart_response(headers, body):
    """
    Parses a multipart/mixed response that contains:
    - One part with Content-Type: application/json     → centroids dict
    - One part with Content-Type: application/octet-stream → NRRD binary data
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

    # Return in same order as original TS → data first, then centroids
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
            print(f"Warning: NRRD data type is {data_array.dtype}, converting to uint16")
            data_array = data_array.astype(np.uint16)

        return data_array, header

    except Exception as e:
        # If pynrrd fails → fallback to manual gzip handling (rare)
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
                raise e
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
        except Exception as e:
            logger.error(f"Error fetching labels for {volume_uid}: {e}")
            return []

    def pick_latest_or_auto(labels, chosen_path="interactive", selected_auto_model=None, target_label=None):
        if not labels:
            return None

        # --- Automatic path ---
        if chosen_path == "automatic" and selected_auto_model:
            # 1st priority: mask produced by the EXACT model FOR THE EXACT ORGAN.
            # Both conditions must hold:
            #   (a) model name appears in segmentation_type  (e.g. "vista")
            #   (b) organ extracted from segmentation_type   == target_label
            #
            # Without (b), a "heart-vista-..." mask would be picked when
            # segmenting "liver" just because the model name matches.
            for label in labels:
                seg_type = label.get("segmentation_type", "")
                model_matches = selected_auto_model in seg_type
                organ_matches = matches_label(seg_type, target_label) if target_label else True
                if model_matches and organ_matches:
                    logger.info(
                        f"1st-priority match: model='{selected_auto_model}' + "
                        f"organ='{target_label}' in segmentation_type='{seg_type}' "
                        f"-> UID={label.get('label_series_instance_uid')}"
                    )
                    return label
                elif model_matches and not organ_matches:
                    # Warn explicitly so the mismatch is visible in the logs.
                    logger.warning(
                        f"Model '{selected_auto_model}' found but WRONG ORGAN: "
                        f"segmentation_type='{seg_type}', expected='{target_label}' — skipping."
                    )

            # 2nd priority: any mask whose organ matches the target (any model).
            # Covers the case where a voxtell-liver mask exists but the plan
            # chose vista for liver — still correct to reuse it.
            if target_label:
                for label in labels:
                    seg_type = label.get("segmentation_type", "")
                    if matches_label(seg_type, target_label):
                        logger.info(
                            f"2nd-priority: no model+organ match; reusing existing "
                            f"'{target_label}' mask (any model) — "
                            f"UID: {label.get('label_series_instance_uid')} | "
                            f"segmentation_type: '{seg_type}'"
                        )
                        return label

            # No match found — do NOT fall back to latest timestamp, that risks
            # pairing the volume with a mask from a completely different organ.
            logger.warning(
                f"No label matched (model='{selected_auto_model}' AND organ='{target_label}') "
                f"or organ-only fallback — skipping this volume to avoid wrong-organ pairing."
            )
            return None

        # --- Interactive / VoxTell path ---
        # Apply the same model+organ dual-check so a wrong-organ mask is never
        # picked during interactive runs either.
        if chosen_path == "interactive":
            if selected_auto_model and target_label:
                for label in labels:
                    seg_type = label.get("segmentation_type", "")
                    if selected_auto_model in seg_type and matches_label(seg_type, target_label):
                        logger.info(
                            f"Interactive: exact model+organ match "
                            f"(model='{selected_auto_model}', organ='{target_label}'): "
                            f"{label.get('label_series_instance_uid')}"
                        )
                        return label
            elif selected_auto_model:
                # No target_label provided — model-name match only (degenerate case).
                for label in labels:
                    if selected_auto_model in label.get("segmentation_type", ""):
                        logger.info(
                            f"Interactive: model-only match for '{selected_auto_model}': "
                            f"{label.get('label_series_instance_uid')}"
                        )
                        return label
            # Fall back to newest label — safe because we just ran segmentation.
            latest = max(labels, key=lambda x: x.get("created_at", ""))
            logger.info(
                f"Interactive path: no exact model+organ match, "
                f"using newest label: {latest.get('label_series_instance_uid')}"
            )
            return latest

        # Only reach here when no model name is given at all — safe fallback.
        return max(labels, key=lambda x: x.get("created_at", ""))

    def trigger_download(url, task_name, uids_list):
        if not uids_list:
            return False
        logger.info(f"POST {url} | task={task_name} | uids={uids_list}")
        try:
            r = requests.post(url, params={"task_name": task_name}, json=uids_list, timeout=60)
            logger.info(f"Trigger response: {r.status_code}")
            if r.status_code != 200:
                logger.error(f"Trigger failed: {r.text[:300]}")
                return False
            return True
        except Exception as e:
            logger.error(f"Trigger exception: {e}")
            return False

    async def wait_and_find(search_dir, uid_to_search_key, timeout_s=120, poll_interval=3.0):
        """
        Waits for NIfTI files to appear in *search_dir*.

        Parameters
        ----------
        uid_to_search_key : dict | list
            * dict  → maps an arbitrary key (e.g. label_uid) to the substring
                      that should appear in the filename (e.g. volume_uid).
                      `found` is keyed by the *dict key*.
            * list  → each element is both the key and the search substring
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
                            logger.info(f"Found [{key}] → {full_path}")

            if remaining_keys:
                elapsed = timeout_s - (deadline - time.time())
                logger.debug(
                    f"Found {len(found)}/{len(uid_to_search_key)} — "
                    f"waiting... ({elapsed:.0f}s elapsed)"
                )
                await asyncio.sleep(poll_interval)

        if remaining_keys:
            missing_info = {k: uid_to_search_key[k] for k in remaining_keys}
            logger.warning(f"Timeout — missing keys/search-substrings: {missing_info}")
        return found

    def copy_volume(src_path, volume_uid, dest_dir):
        dst = os.path.join(dest_dir, f"{volume_uid}.nii.gz")
        shutil.copy2(src_path, dst)
        size_mb = os.path.getsize(dst) / 1_048_576
        logger.info(f"Copied volume → {dst} ({size_mb:.2f} MB)")
        return dst

    def copy_and_normalize_label(src_path, volume_uid, dest_dir):
        dst = os.path.join(dest_dir, f"{volume_uid}.nii.gz")
        try:
            nii  = nib.load(src_path)
            
            # --- MEMORY SAFETY CHECK ---
            # Calculate total voxels from the shape (width * height * depth)
            total_voxels = np.prod(nii.shape)
            # get_fdata() casts to float64, which is 8 bytes per voxel
            estimated_ram_bytes = total_voxels * 8
            estimated_ram_gb = estimated_ram_bytes / (1024**3)
            
            # If the uncompressed array will take more than 16 GB of RAM, skip it to prevent crash
            if estimated_ram_gb > 16.0:
                logger.error(f"Skipping label {volume_uid}: requires {estimated_ram_gb:.2f} GB of RAM, exceeding the 16 GB safety limit.")
                return "SKIPPED_OOM"
            # ---------------------------

            data = nii.get_fdata()
            logger.info(f"Label unique values before normalize: {np.unique(data)}")
            if np.max(data) == 255:
                data = (data > 0).astype(np.uint8)
                logger.info("Converted {0,255} → {0,1}")
            nib.save(nib.Nifti1Image(data, nii.affine, nii.header), dst)
            size_mb = os.path.getsize(dst) / 1_048_576
            logger.info(f"Saved normalized label → {dst} ({size_mb:.2f} MB)")
            return dst
        except Exception as e:
            logger.error(f"Error normalizing label {src_path}: {e}")
            return None

    # ------------------------------------------------------------------ #
    #  1. pair volumes ↔ labels                                          #
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
            continue
        label_uid = latest.get("label_series_instance_uid")
        if not label_uid:
            continue
        vol_uid_confirmed = latest.get("volume_series_instance_uid") or vol_uid
        logger.info(f"Paired: volume={vol_uid_confirmed}  label={label_uid}")
        volumes_to_process.append(vol_uid_confirmed)
        labels_to_process.append(label_uid)

    if not volumes_to_process:
        logger.error("Nothing to process — aborting.")
        return []

    logger.info(f"Pairs ready: {len(volumes_to_process)}")

    # ------------------------------------------------------------------ #
    #  2. trigger downloads (POST → MONAI saves to its local dirs)       #
    # ------------------------------------------------------------------ #
    if download_volumes:
        trigger_download(DOWNLOAD_IMAGES_URL, TASK_NAME, volumes_to_process)
    
    trigger_download(DOWNLOAD_LABELS_URL, TASK_NAME, labels_to_process)

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
                f"MONAI_LABEL_DIR='{MONAI_LABEL_DIR}' — "
                f"{len(existing)} .nii.gz file(s) already present. "
                f"Sample: {existing[:5]}"
            )
        else:
            logger.warning(f"MONAI_LABEL_DIR does not exist yet: {MONAI_LABEL_DIR}")
    except Exception as _diag_err:
        logger.warning(f"Diagnostic scan failed: {_diag_err}")

    # Search by label_uid in filename (MONAI saves as {label_uid}.nii.gz).
    # found_labels is keyed by label_uid → file path.
    found_labels = await wait_and_find(
        MONAI_LABEL_DIR,
        labels_to_process,   # plain list: key == search substring == label_uid
        timeout_s=600
    )
    logger.info(
        f"Label files found: {len(found_labels)}/{len(labels_to_process)} — "
        f"keys: {list(found_labels.keys())[:3]}..."
    )

    # ------------------------------------------------------------------ #
    #  4. copy to workspace                                              #
    # ------------------------------------------------------------------ #
    successful = []
    skipped_oom = []
    for vol_uid, label_uid in zip(volumes_to_process, labels_to_process):
        label_src = found_labels.get(label_uid)
        if not label_src:
            logger.error(
                f"Label file never appeared for volume={vol_uid} "
                f"(label_uid={label_uid}) — skipping pair"
            )
            continue

        vol_result = True  # Default to True if we aren't downloading volumes
        if download_volumes:
            vol_src = found_volumes.get(vol_uid)
            if not vol_src:
                logger.error(f"Volume file never appeared for {vol_uid} — skipping pair")
                continue
            vol_result = copy_volume(vol_src, vol_uid, FINAL_DIR_IMG)

        label_result = copy_and_normalize_label(label_src, vol_uid, FINAL_DIR_LABEL)

        if label_result == "SKIPPED_OOM":
            skipped_oom.append(vol_uid)
            continue

        if vol_result and label_result:
            successful.append(vol_uid)
        else:
            logger.error(f"Failed to copy vol/label for {vol_uid}")

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

            # warn and cap against shape mismatch in Z >> problem happens with subset of vol !
            # todo: check why seg returns sometimes the mask not over all sliceson subset only 
            if vol_data.shape != mask_data.shape:
                logger.warning(
                    f"Shape mismatch for {uid}: vol={vol_data.shape}, mask={mask_data.shape}. "
                    f"Capping Z to min of both."
                )
            max_z = min(vol_data.shape[2], mask_data.shape[2])

            # safe zone
            z_indices = np.any(mask_data[:, :, :max_z] > 0, axis=(0, 1))
            active_slices = np.where(z_indices)[0]

            if len(active_slices) == 0:
                logger.warning(f"Empty mask for {uid}.")
                continue

            # padding
            start_z = max(0, active_slices[0] - 2)
            end_z = min(max_z - 1, active_slices[-1] + 2)

            frames = []
            for z in range(start_z, end_z + 1, 2):
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

                # overlay
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
                    "gifUrl": f"http://localhost:8150/files/{workspace_folder_name}/overlays/{gif_filename}"
                }
                generated_volumes.append(volume_entry)
                logger.info(f"Generated GIF for {uid}")

                # ── Progressive broadcast: notify caller immediately ──
                if on_gif_ready is not None:
                    try:
                        on_gif_ready(volume_entry)
                    except Exception as cb_err:
                        logger.warning(f"on_gif_ready callback failed for {uid}: {cb_err}")

        except Exception as e:
            logger.error(f"Failed to generate GIF for {uid}: {e}", exc_info=True)
        finally:
            # Force garbage collection to prevent CPU OOM
            if 'vol_data' in locals(): del vol_data
            if 'mask_data' in locals(): del mask_data
            if 'frames' in locals(): del frames
            import gc
            gc.collect()

    return generated_volumes

#  ================================
#  check the cababilities of voxtell
#  ================================

def normalize_label(label: str) -> str:
    """Normalize an LLM-generated label to the space-separated form used in supported.csv and the VoxTell cache.
    e.g.  'lung_nodule' -> 'lung nodule',  'Pulmonary_Nodule' -> 'pulmonary nodule'
    """
    return label.replace("_", " ").lower().strip()


def check_voxtell_label_availability(target_label):
    """
    Checks if Voxtell can generate labels for the given target anatomical labels.
    Returns the normalized label string (which is the correct cache key) if supported,
    False if not found, None if the file is missing.
    """
    # Normalize: LLM may output 'lung_nodule' but CSV/cache has 'lung nodule'
    normalized = normalize_label(target_label)

    csv_filepath = Path(__file__).parent.parent.parent.parent / "supported.csv"
    try:
        with open(csv_filepath, mode='r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                prompt = normalize_label(row['Prompt'])
                if normalized == prompt:
                    return normalized   # return the canonical form for use as cache key
    except FileNotFoundError:
        logger.warning(f"supported.csv not found at {csv_filepath}")
        return None

    # Label not present in the CSV
    return False
# ==================================
#  get the Benchmark dice score of selected model (todo: should be in db)
# ==================================

def get_model_dice_score(model_name, target_label):
    """
    Returns the benchmark Dice score for the given model and target label.
    Reads from supported.csv; falls back to 90.0 if the label is not found or the file is unreadable.
    """
    DEFAULT_DICE = 90.0

    if model_name and model_name.lower() == "voxtell":
        try:
            csv_filepath = Path(__file__).parent.parent.parent.parent / "supported.csv"
            df = pd.read_csv(csv_filepath)
            mask = df['Prompt'].apply(normalize_label) == normalize_label(target_label)
            matches = df.loc[mask, 'VoxTell']
            if matches.empty:
                logger.info(
                    f"Label '{target_label}' not found in supported.csv for {model_name}; "
                    f"using default Dice score {DEFAULT_DICE}"
                )
                dice_score = DEFAULT_DICE
            else:
                dice_score = matches.iloc[0]
        except FileNotFoundError:
            logger.warning(f"supported.csv not found; returning default Dice score {DEFAULT_DICE}")
            dice_score = DEFAULT_DICE
        except Exception as e:
            logger.warning(f"Failed to read dice score from supported.csv for {model_name}: {e}; returning default {DEFAULT_DICE}")
            dice_score = DEFAULT_DICE
    else:
        dice_score = DEFAULT_DICE  # default score for other models (todo: should be in db)
    return float(dice_score)

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

    # cleanup masks (labels)
    if os.path.exists(masks_dir):
        for fname in os.listdir(masks_dir):
            if fname.lower().endswith(".nii.gz"):
                uid = fname.replace(".nii.gz", "")
                if uid not in accepted_uids:
                    os.remove(os.path.join(masks_dir, fname))
                    logger.info(f"Removed mask (discarded or pending modification): {fname}")
