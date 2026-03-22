import re
import gzip
import io
import os
import json
import shutil
import time
import asyncio
from anyio import Path
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

            # Empty list → no mask
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
    url = f"http://localhost:8000/segmentations/by-volume/{volume_uid}"
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
                json_data = eval(json_text)          # safer: json.loads(json_text)
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

        # Start of actual part content = after boundary + \r\n
        part_start = boundary_index + len(boundary) + 2
        next_boundary = find_subarray(buffer, boundary, part_start)

        part_end = next_boundary if next_boundary != -1 else len(buffer)

        # Exclude trailing \r\n before next boundary or end
        part = buffer[part_start : part_end - 2]
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
async def download_and_pair_volumes(datasetlist, chosen_path, selected_auto_model_for_volume):
    """
    Downloads volumes and their associated labels.
    """
    VOLUMES = datasetlist
    LABELS_BY_VOLUME_URL = "http://localhost:8000/segmentations/by-volume/"
    DOWNLOAD_IMAGES_URL = "http://localhost:8000/datastore/images"
    DOWNLOAD_LABELS_URL = "http://localhost:8000/datastore/labels"
    TASK_NAME = "final"

    current_file = Path(__file__)
    current_dir = current_file.parent.parent.parent.parent

    DOWNLOAD_DIR_IMG = os.path.join(current_dir, "apps", "radiology", "General_Data")
    DOWNLOAD_DIR_LABEL = os.path.join(current_dir, "apps", "radiology", "Labels")
    FINAL_DIR_IMG = os.path.join(current_dir, "agent_system", "workspace", "volumes")
    FINAL_DIR_LABEL = os.path.join(current_dir, "agent_system", "workspace", "masks")

    os.makedirs(DOWNLOAD_DIR_IMG, exist_ok=True)
    os.makedirs(DOWNLOAD_DIR_LABEL, exist_ok=True)
    os.makedirs(FINAL_DIR_IMG, exist_ok=True)
    os.makedirs(FINAL_DIR_LABEL, exist_ok=True)

    def fetch_labels_for_volume(volume_uid):
        """Fetch all labels associated with a volume from the API"""
        url = LABELS_BY_VOLUME_URL + volume_uid
        logger.info(f"Fetching labels from: {url}")
        try:
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                logger.warning(f"GET {url} returned {r.status_code}: {r.text}")
                return []
            labels = r.json()
            logger.info(f"Received {len(labels)} labels for volume {volume_uid}")
            if labels:
                logger.debug(f"Labels data: {json.dumps(labels, indent=2)}")
            return labels
        except Exception as e:
            logger.error(f"Error fetching labels for {volume_uid}: {e}")
            return []

    def pick_latest_or_auto(labels, chosen_path="interactive", selected_auto_model=None):
        """
        Select the appropriate label based on the path:
        - automatic: Match segmentation_type with selected_auto_model
        - interactive: Pick the latest by created_at
        """
        if not labels:
            logger.warning("No labels provided to pick_latest_or_auto")
            return None
        
        logger.info(f"Picking label from {len(labels)} options (path={chosen_path}, model={selected_auto_model})")
        
        # match model for automatic path
        if chosen_path == "automatic" and selected_auto_model:
            logger.info(f"Searching for label with segmentation_type='{selected_auto_model}'")
            for label in labels:
                seg_type = label.get("segmentation_type")
                logger.debug(f"Checking label: segmentation_type='{seg_type}', created_at='{label.get('created_at')}'")
                if seg_type == selected_auto_model:
                    logger.info(f" Found matching automatic label: {label.get('label_series_instance_uid')}")
                    return label
            logger.warning(f"No label found with segmentation_type='{selected_auto_model}', falling back to latest")
        
        # the latest created mask
        latest = max(labels, key=lambda x: x.get("created_at", ""))
        logger.info(f"Selected latest label: {latest.get('label_series_instance_uid')} (created_at={latest.get('created_at')})")
        return latest

    def post_trigger_download(url, task_name, uids_list):
        """Trigger download API endpoint"""
        if not uids_list:
            logger.warning(f"Empty UID list for {url}, skipping")
            return None
        
        logger.info(f"POST to {url} with {len(uids_list)} UIDs: {uids_list}")
        try:
            response = requests.post(url, params={"task_name": task_name}, json=uids_list, timeout=60)
            logger.info(f"Download trigger response: {response.status_code}")
            if response.status_code != 200:
                logger.error(f"Download failed: {response.text}")
            return response
        except Exception as e:
            logger.error(f"ERROR posting to {url}: {e}")
            return None

    async def wait_for_files_appear(target_dir, expected_uids, timeout_s=60, poll_interval=2.0):
        """Wait for files to appear in the download directory"""
        if not expected_uids:
            logger.info("No UIDs to wait for")
            return set()
        
        deadline = time.time() + timeout_s
        found = set()
        logger.info(f"Waiting for {len(expected_uids)} files in {target_dir}")
        logger.info(f"Expected UIDs: {expected_uids}")
        
        while time.time() < deadline:
            current_files = os.listdir(target_dir)
            for name in current_files:
                if not name.lower().endswith(".nii.gz"):
                    continue
                for uid in expected_uids:
                    if uid in name and name not in found:
                        found.add(name)
                        logger.info(f"Found file: {name}")
            
            if len(found) >= len(expected_uids):
                logger.info(f"All {len(expected_uids)} files found!")
                return found
            
            remaining = time.time() - deadline
            logger.debug(f"Found {len(found)}/{len(expected_uids)} files, waiting... ({remaining:.1f}s remaining)")
            await asyncio.sleep(poll_interval)
        
        logger.warning(f"Timeout Only found {len(found)}/{len(expected_uids)} files: {found}")
        return found

    def move_matching_niigz(src_dir, dst_dir, matching_uids=None):
        """Move volume files from source to destination"""
        moved = []
        for fname in os.listdir(src_dir):
            if not fname.lower().endswith(".nii.gz"):
                continue
            if matching_uids and not any(uid in fname for uid in matching_uids):
                continue
            src_path = os.path.join(src_dir, fname)
            dst_path = os.path.join(dst_dir, fname)
            shutil.move(src_path, dst_path)
            moved.append(fname)
            logger.info(f"Moved volume: {fname}")
        logger.info(f"Total volumes moved: {len(moved)}")
        return moved
    def move_and_rename_labels(src_dir, dst_dir, label_uids, volume_uids):
        """
        Move label files, convert {0,255} -> {0,1}, and rename them
        to match their corresponding volume UIDs.
        """
        if not label_uids or not volume_uids:
            logger.warning("Empty label or volume UID lists")
            return

        logger.info(f"Moving, renaming, and normalizing {len(label_uids)} labels")
        moved_count = 0

        for label_uid, volume_uid in zip(label_uids, volume_uids):
            label_file = None
            for fname in os.listdir(src_dir):
                if label_uid in fname and fname.lower().endswith(".nii.gz"):
                    label_file = fname
                    break

            if not label_file:
                logger.warning(f"Label file not found for UID: {label_uid}")
                continue

            src_path = os.path.join(src_dir, label_file)
            dst_path = os.path.join(dst_dir, f"{volume_uid}.nii.gz")

            try:
                nii = nib.load(src_path)
                data = nii.get_fdata()

                unique_vals = np.unique(data)
                logger.info(f"Label {label_file} unique values before: {unique_vals}")

                # convert {0,255} → {0,1}
                if np.max(data) == 255:
                    data = (data > 0).astype(np.uint8)
                    logger.info(f"Converted label {label_file} from {{0,255}} to {{0,1}}")
                # the new updated one    
                new_nii = nib.Nifti1Image(data, affine=nii.affine, header=nii.header)
                nib.save(new_nii, dst_path)

                os.remove(src_path)

                moved_count += 1
                logger.info(f" Renamed + normalized label → {volume_uid}.nii.gz")

            except Exception as e:
                logger.error(f"Error processing label {label_file}: {e}")

        logger.info(f"Total labels moved and normalized: {moved_count}/{len(label_uids)}")

    # Main pairing logic
    volumes_to_download = []
    labels_to_download = []

    logger.info(f"Processing {len(VOLUMES)} volumes for label pairing...")
    
    for idx, vol_uid in enumerate(VOLUMES, 1):
        logger.info(f"[{idx}/{len(VOLUMES)}] Processing volume: {vol_uid}")
        
        # Fetch all labels for this volume
        labels = fetch_labels_for_volume(vol_uid)
        
        if not labels:
            logger.warning(f"No labels found for volume {vol_uid}, skipping")
            continue
        
        # Select the appropriate label based on path
        selected_model = selected_auto_model_for_volume if chosen_path == "automatic" else None
        latest = pick_latest_or_auto(labels, chosen_path=chosen_path, selected_auto_model=selected_model)
        
        if latest is None:
            logger.warning(f"No suitable label selected for volume {vol_uid}, skipping")
            continue
        
        # Extract UIDs from the selected label
        label_uid = latest.get("label_series_instance_uid")
        vol_uid_from_label = latest.get("volume_series_instance_uid") or vol_uid
        
        if not label_uid:
            logger.warning(f"Label UID is empty for volume {vol_uid}, skipping")
            continue
        
        logger.info(f"Paired: volume={vol_uid_from_label} label={label_uid}")
        volumes_to_download.append(vol_uid_from_label)
        labels_to_download.append(label_uid)

    logger.info("="*60)
    logger.info(f"PAIRING COMPLETE:")
    logger.info(f"Volumes to download: {len(volumes_to_download)}")
    logger.info(f"Labels to download: {len(labels_to_download)}")
    logger.info("="*60)

    if not volumes_to_download or not labels_to_download:
        logger.error(" No volumes or labels to download! Exiting.")
        return

    # Trigger downloads
    logger.info("Triggering volume download...")
    vol_response = post_trigger_download(DOWNLOAD_IMAGES_URL, TASK_NAME, volumes_to_download)
    
    logger.info("Triggering label download...")
    label_response = post_trigger_download(DOWNLOAD_LABELS_URL, TASK_NAME, labels_to_download)

    # Wait for files to appear in download directories
    logger.info("Waiting for volume files to appear...")
    found_volume_files = await wait_for_files_appear(DOWNLOAD_DIR_IMG, volumes_to_download, timeout_s=90)
    
    logger.info("Waiting for label files to appear...")
    found_label_files = await wait_for_files_appear(DOWNLOAD_DIR_LABEL, labels_to_download, timeout_s=90)

    logger.info("Moving volume files to final directory...")
    move_matching_niigz(DOWNLOAD_DIR_IMG, FINAL_DIR_IMG, volumes_to_download)
    
    logger.info("Moving and renaming label files...")
    move_and_rename_labels(DOWNLOAD_DIR_LABEL, FINAL_DIR_LABEL, labels_to_download, volumes_to_download)
    
    logger.info("="*60)
    logger.info("DOWNLOAD AND PAIRING COMPLETED SUCCESSFULLY!")

