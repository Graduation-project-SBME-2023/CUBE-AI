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
async def check_having_mask(volume_uid):
    """
    Checks if a segmentation mask exists for the given volume SeriesInstanceUID (volume_uid).
    Calls the endpoint /segmentations/by-volume/{volume_uid} and returns the list of segmentations.
    """
    url = f"{MONAI_URL}/segmentations/by-volume/{volume_uid}"
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.get(url)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError(
            f"Expected a segmentation list for volume {volume_uid}, "
            f"received {type(payload).__name__}"
        )
    return payload

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

async def download_and_pair_volumes(
    workspace_dir,
    resolved_pairs,
    download_volumes=True,
    task_name="segmentation_task",
):
    DOWNLOAD_IMAGES_URL  = f"{MONAI_URL}/datastore/images"
    DOWNLOAD_LABELS_URL  = f"{MONAI_URL}/datastore/labels"
    TASK_NAME = task_name

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

    def copy_and_normalize_label(src_path, volume_uid, dest_dir, target_id=None):
        """
        Normalize a segmentation label NIfTI and save it as a binary {0,1} mask.

        Handles two formats:
          1. Binary {0, 255}  – simple threshold to {0,1}.
          2. Binary {0, 1}    – already correct, save as-is.
        """
        filename = (
            f"{volume_uid}__{target_id}.nii.gz"
            if target_id
            else f"{volume_uid}.nii.gz"
        )
        dst = os.path.join(dest_dir, filename)
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
            else:
                # Already binary {0, 1} or any other value — binarize with >0
                data = (data > 0).astype(np.uint8)
                logger.info("Label binarized with >0; keeping as-is.")

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
    labels_to_process = []
    target_ids_to_process = []

    for pair in resolved_pairs:
        volume_uid = str(pair.get("volume_uid") or "").strip()
        label_uid = str(pair.get("label_uid") or "").strip()
        target_id = str(pair.get("target_id") or "").strip()
        if not volume_uid or not label_uid or not target_id:
            logger.error("Invalid resolved segmentation pair: %s", pair)
            continue
        volumes_to_process.append(volume_uid)
        labels_to_process.append(label_uid)
        target_ids_to_process.append(target_id)

    if not volumes_to_process:
        logger.error("Nothing to process; aborting.")
        return [], []

    logger.info(f"Pairs ready: {len(volumes_to_process)}")

    # ------------------------------------------------------------------ #
    #  2. trigger downloads (POST; MONAI saves to its local dirs)        #
    # ------------------------------------------------------------------ #
    if download_volumes:
        trigger_download(
            DOWNLOAD_IMAGES_URL,
            TASK_NAME,
            list(dict.fromkeys(volumes_to_process)),
        )
    
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

    successful = []
    skipped_oom = []
    for vol_uid, label_uid, target_id in zip(
        volumes_to_process, labels_to_process, target_ids_to_process
    ):
        label_src = found_labels.get(label_uid) if label_uid else None
        if not label_src and label_uid:
            logger.error(
                f"Label file never appeared for volume={vol_uid} "
                f"(label_uid={label_uid}); skipping pair"
            )
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
                label_src, vol_uid, FINAL_DIR_LABEL, target_id=target_id,
            )
        else:
            label_result = True # No label to process for interactive manual

        if label_result == "SKIPPED_OOM":
            skipped_oom.append({"volume_uid": vol_uid, "target_id": target_id})
            continue

        if vol_result and label_result:
            successful.append({"volume_uid": vol_uid, "target_id": target_id})
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
        uids: list of SeriesInstanceUIDs or dictionaries to process. Dictionary
              entries may provide mask_path to render an uploaded modification.
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

    for item in uids:
        if isinstance(item, dict):
            uid = str(item.get("volume_uid") or item.get("seriesInstanceUid") or "")
            target_id = str(item.get("target_id") or item.get("targetId") or "")
            target_label = item.get("canonical_label") or item.get("targetLabel")
            mask_path_override = str(item.get("mask_path") or "")
        else:
            uid = str(item)
            target_id = ""
            target_label = None
            mask_path_override = ""
        pair_id = f"{uid}__{target_id}" if target_id else uid
        vol_path = os.path.join(volumes_dir, f"{uid}.nii.gz")
        default_mask_path = os.path.join(masks_dir, f"{pair_id}.nii.gz")
        if mask_path_override:
            mask_path = (
                mask_path_override
                if os.path.isabs(mask_path_override)
                else os.path.join(workspace_dir, mask_path_override)
            )
            # Older manifests may contain a stale relative path. Prefer the
            # canonical pair path when it exists so preview generation remains
            # backwards-compatible.
            if not os.path.exists(mask_path) and os.path.exists(default_mask_path):
                logger.warning(
                    "Configured mask path does not exist for %s: %s. "
                    "Falling back to %s.",
                    pair_id,
                    mask_path,
                    default_mask_path,
                )
                mask_path = default_mask_path
        else:
            mask_path = default_mask_path
        gif_filename = f"{pair_id}.gif"
        gif_path = os.path.join(output_dir, gif_filename)

        if not os.path.exists(vol_path) or not os.path.exists(mask_path):
            logger.warning(
                "Missing files for %s. volume=%s (exists=%s), mask=%s "
                "(exists=%s). Skipping GIF generation.",
                pair_id,
                vol_path,
                os.path.exists(vol_path),
                mask_path,
                os.path.exists(mask_path),
            )
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

            # Diagnostic: compare full-mask signal vs. capped-Z signal
            full_mask_unique = np.unique(mask_data)
            capped_mask_unique = np.unique(mask_data[:, :, :max_z])
            logger.info(f"Mask unique (full): {full_mask_unique}  |  unique (capped to Z={max_z}): {capped_mask_unique}")

            # safe zone — search within capped range first
            z_indices = np.any(mask_data[:, :, :max_z] > 0, axis=(0, 1))
            active_slices = np.where(z_indices)[0]

            # If the capped range has no signal but the full mask does, the tumor
            # lives in slices beyond max_z (shape mismatch where mask > volume in Z).
            # Fall back to searching the full mask and clamping slice indices to max_z-1.
            if len(active_slices) == 0 and np.any(mask_data > 0):
                logger.warning(
                    f"Mask for {uid} has signal outside the capped Z range "
                    f"(max_z={max_z}, mask.shape[2]={mask_data.shape[2]}). "
                    f"Falling back to full-mask Z search."
                )
                z_indices_full = np.any(mask_data > 0, axis=(0, 1))
                active_slices_full = np.where(z_indices_full)[0]
                # Clamp to the volume's Z extent so we don't index out-of-bounds
                active_slices = active_slices_full[active_slices_full < max_z]
                if len(active_slices) == 0 and len(active_slices_full) > 0:
                    # All signal is completely out of the volume's Z range — use centre
                    logger.warning(
                        f"All mask signal for {uid} is beyond the volume Z range; "
                        f"rendering centre slices as preview."
                    )

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
                    "pairId": pair_id,
                    "targetId": target_id or None,
                    "targetLabel": target_label,
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
    accepted_keys = set(accepted_uids)
    modified_keys = set(modified_uids)
    pair_keys_to_keep = accepted_keys | modified_keys
    uids_to_keep_volumes = {
        key.split("__", 1)[0] for key in pair_keys_to_keep
    }
    
    # cleanup volumes
    if os.path.exists(volumes_dir):
        for fname in os.listdir(volumes_dir):
            if fname.lower().endswith(".nii.gz"):
                uid = fname.replace(".nii.gz", "")
                if uid not in uids_to_keep_volumes:
                    os.remove(os.path.join(volumes_dir, fname))
                    logger.info(f"Removed discarded volume: {fname}")

    # cleanup masks (labels) — keep for both accepted AND modified
    if os.path.exists(masks_dir):
        for fname in os.listdir(masks_dir):
            if fname.lower().endswith(".nii.gz"):
                pair_key = fname.replace(".nii.gz", "")
                volume_uid = pair_key.split("__", 1)[0]
                keep = pair_key in pair_keys_to_keep or volume_uid in pair_keys_to_keep
                if not keep:
                    os.remove(os.path.join(masks_dir, fname))
                    logger.info(f"Removed mask (discarded): {fname}")
