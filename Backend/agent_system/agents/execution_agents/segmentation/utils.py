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

def matches_label(segmentation_type, label):

    seg_norm = "".join(c for c in segmentation_type.lower() if c.isalpha())
    label_norm = "".join(c for c in label.lower() if c.isalpha())

    return label_norm in seg_norm

async def exclude_existing_masks(volumes, labels):

    eligible = []

    for volume_uid in volumes:
        has_mask = False
        logger.info(f"Checking masks: volume={volume_uid}, labels={labels}")

        try:
            segmentations = await check_having_mask(volume_uid)

            # empty list → no mask
            if not segmentations:
                has_mask = False
            else:
                # Smart label-based search
                for seg in segmentations:
                    seg_type = seg.get("segmentation_type", "")
                    for label in labels:
                        if matches_label(seg_type, label):
                            has_mask = True
                            logger.info(
                                f"Match found: volume={volume_uid}, "
                                f"label={label}, segmentation_type={seg_type}"
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
            logger.info(f"Volume {volume_uid} excluded (matching mask exists)")

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
    download_volumes=True  
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

    def pick_latest_or_auto(labels, chosen_path="interactive", selected_auto_model=None):
        if not labels:
            return None

        # --- Automatic path: must match the exact model that ran segmentation ---
        if chosen_path == "automatic" and selected_auto_model:
            for label in labels:
                if selected_auto_model in label.get("segmentation_type", ""):
                    logger.info(f"Found matching automatic label: {label.get('label_series_instance_uid')}")
                    return label
            # Do NOT fall back to the latest timestamp here.
            # That would pair the volume with an old mask from a different organ
            # (e.g., a spleen mask when the hypothesis is about lungs).
            logger.warning(
                f"No label matched automatic model '{selected_auto_model}' — "
                f"skipping this volume to avoid wrong-organ pairing."
            )
            return None

        # --- Interactive / VoxTell path ---
        # First try an exact model-name match in segmentation_type.
        # If that fails (MONAI Label stores its own internal type strings, not
        # the model name), fall back to the most recently created label.
        # This is safe because run_interactive_pipeline just finished running
        # voxtell on all eligible volumes, so the newest label is the one we want.
        if chosen_path == "interactive":
            if selected_auto_model:
                for label in labels:
                    if selected_auto_model in label.get("segmentation_type", ""):
                        logger.info(
                            f"Found matching interactive label ({selected_auto_model}): "
                            f"{label.get('label_series_instance_uid')}"
                        )
                        return label
            # Fall back to newest label — safe because we just ran segmentation.
            latest = max(labels, key=lambda x: x.get("created_at", ""))
            logger.info(
                f"Interactive path: no exact model match for '{selected_auto_model}', "
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

    async def wait_and_find(search_dir, uids, timeout_s=120, poll_interval=3.0):
        deadline  = time.time() + timeout_s
        found     = {}
        remaining_uids = set(uids)

        logger.info(f"Waiting for {len(uids)} file(s) in {search_dir} (timeout={timeout_s}s)")

        while time.time() < deadline and remaining_uids:
            if not os.path.isdir(search_dir):
                logger.warning(f"Directory does not exist yet: {search_dir}")
                await asyncio.sleep(poll_interval)
                continue

            for root, _, files in os.walk(search_dir):
                for fname in files:
                    if not fname.lower().endswith(".nii.gz"):
                        continue
                    for uid in list(remaining_uids):
                        if uid in fname:
                            full_path = os.path.join(root, fname)
                            found[uid] = full_path
                            remaining_uids.discard(uid)
                            logger.info(f"Found: {full_path}")

            if remaining_uids:
                elapsed = timeout_s - (deadline - time.time())
                logger.debug(f"Found {len(found)}/{len(uids)} — waiting... ({elapsed:.0f}s elapsed)")
                await asyncio.sleep(poll_interval)

        if remaining_uids:
            logger.warning(f"Timeout — missing UIDs: {remaining_uids}")
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
        latest = pick_latest_or_auto(labels, chosen_path=chosen_path, selected_auto_model=selected_model)
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
    found_volumes = {}
    if download_volumes:
        logger.info("Waiting for images to appear...")
        found_volumes = await wait_and_find(MONAI_IMG_DIR, volumes_to_process, timeout_s=600)
        
    logger.info("Waiting for labels to appear...")
    found_labels = await wait_and_find(MONAI_LABEL_DIR, labels_to_process, timeout_s=600)

    # ------------------------------------------------------------------ #
    #  4. copy to workspace                                              #
    # ------------------------------------------------------------------ #
    successful = []
    skipped_oom = []
    for vol_uid, label_uid in zip(volumes_to_process, labels_to_process):
        label_src = found_labels.get(label_uid)
        if not label_src:
            logger.error(f"Label file never appeared for {label_uid} — skipping pair")
            continue

        vol_result = True # Default to True if we aren't downloading volumes
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

    # cleanup masks (labels) — keep for both accepted AND modified
    uids_to_keep_masks = set(accepted_uids) | set(modified_uids)
    if os.path.exists(masks_dir):
        for fname in os.listdir(masks_dir):
            if fname.lower().endswith(".nii.gz"):
                uid = fname.replace(".nii.gz", "")
                if uid not in uids_to_keep_masks:
                    os.remove(os.path.join(masks_dir, fname))
                    logger.info(f"Removed mask (discarded): {fname}")
