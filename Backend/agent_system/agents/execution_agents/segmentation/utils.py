import gzip
import os
import json
import shutil
import time
import asyncio
from pathlib import Path
import nrrd
import numpy as np
import tempfile
import httpx
import nibabel as nib
import SimpleITK as sitk
import logging
from email import policy
from email.parser import BytesParser

logger = logging.getLogger(__name__)
from PIL import Image

MONAI_URL = os.environ.get("MONAI_URL", "http://localhost:8000")
REQUEST_TIMEOUT_SECONDS = 10.0
async def check_having_mask(
    volume_uid,
    *,
    client: httpx.AsyncClient | None = None,
):
    """
    Checks if a segmentation mask exists for the given volume SeriesInstanceUID (volume_uid).
    Calls the endpoint /segmentations/by-volume/{volume_uid} and returns the list of segmentations.
    """
    url = f"{MONAI_URL}/segmentations/by-volume/{volume_uid}"
    request_client = client or httpx.AsyncClient()
    try:
        response = await request_client.get(
            url,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    finally:
        if client is None:
            await request_client.aclose()
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
    content_type = str(headers.get("content-type") or "")
    try:
        message = BytesParser(policy=policy.default).parsebytes(
            b"MIME-Version: 1.0\r\nContent-Type: "
            + content_type.encode("ascii")
            + b"\r\n\r\n"
            + body
        )
    except (UnicodeEncodeError, ValueError) as exc:
        raise ValueError(f"Invalid multipart Content-Type: {exc}") from exc
    if not message.is_multipart():
        raise ValueError("Response is not a valid multipart body")

    json_data = None
    nrrd_bytes = None
    for part in message.iter_parts():
        payload = part.get_payload(decode=True)
        if payload is None:
            raise ValueError("Multipart part has no decodable payload")
        if part.get_content_type() == "application/json":
            try:
                json_data = json.loads(payload.decode(part.get_content_charset() or "utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Failed to parse JSON part: {exc}") from exc
        elif part.get_content_type() == "application/octet-stream":
            nrrd_bytes = payload

    if json_data is None or nrrd_bytes is None:
        raise ValueError(
            "Multipart response missing required parts: "
            "need application/json and application/octet-stream"
        )

    if not isinstance(json_data, dict):
        raise ValueError("Multipart JSON part must contain an object")
    return nrrd_bytes, json_data.get('centroids', {})


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
        
        data_array = _validated_nrrd_array(data_array)
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
                return _validated_nrrd_array(data_array).astype(np.uint16), header
            else:
                raise
        except Exception as inner_e:
            raise ValueError(f"Failed to parse NRRD data: {inner_e}") from inner_e


def _validated_nrrd_array(data_array: np.ndarray) -> np.ndarray:
    if not np.issubdtype(data_array.dtype, np.number):
        raise ValueError(f"NRRD mask dtype must be numeric, received {data_array.dtype}")
    if data_array.size == 0:
        raise ValueError("NRRD mask is empty")
    if np.issubdtype(data_array.dtype, np.floating):
        raise ValueError(
            f"NRRD mask dtype must be an integer type, received {data_array.dtype}"
        )
    if not np.all(np.isfinite(data_array)):
        raise ValueError("NRRD mask contains NaN or infinite values")
    min_value = float(np.min(data_array))
    max_value = float(np.max(data_array))
    if min_value < 0 or max_value > np.iinfo(np.uint16).max:
        raise ValueError(
            f"NRRD mask values must be in [0, 65535], received [{min_value}, {max_value}]"
        )
    unique_values = set(np.unique(data_array).tolist())
    if not unique_values <= {0, 1, 255} or ({1, 255} <= unique_values):
        raise ValueError(
            "VoxTell NRRD output must be binary with values {0,1} or {0,255}; "
            f"received {sorted(unique_values)[:20]}"
        )
    return data_array


def ensure_required_patient_tags(datasets):

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

def _geometry_matches(
    image: sitk.Image,
    reference: sitk.Image,
    *,
    tolerance: float = 1e-5,
) -> bool:
    return (
        image.GetDimension() == reference.GetDimension()
        and image.GetSize() == reference.GetSize()
        and np.allclose(image.GetSpacing(), reference.GetSpacing(), atol=tolerance)
        and np.allclose(image.GetOrigin(), reference.GetOrigin(), atol=tolerance)
        and np.allclose(image.GetDirection(), reference.GetDirection(), atol=tolerance)
    )


def align_and_binarize_mask(
    mask_path: str,
    reference_volume_path: str,
    output_path: str,
) -> dict:
    """Align a mask to the reference grid using nearest-neighbor resampling."""
    reference = sitk.ReadImage(reference_volume_path)
    mask = sitk.ReadImage(mask_path)
    original_geometry = {
        "size": list(mask.GetSize()),
        "spacing": list(mask.GetSpacing()),
        "origin": list(mask.GetOrigin()),
        "direction": list(mask.GetDirection()),
    }
    geometry_adjusted = not _geometry_matches(mask, reference)
    if geometry_adjusted:
        resample = sitk.ResampleImageFilter()
        resample.SetReferenceImage(reference)
        resample.SetInterpolator(sitk.sitkNearestNeighbor)
        resample.SetTransform(sitk.Transform())
        resample.SetDefaultPixelValue(0)
        mask = resample.Execute(mask)
        logger.warning(
            "Resampled mask %s to reference geometry from size=%s to size=%s",
            mask_path,
            original_geometry["size"],
            list(reference.GetSize()),
        )

    data = sitk.GetArrayFromImage(mask)
    if not np.all(np.isfinite(data)):
        raise ValueError(f"Mask contains NaN or infinite values: {mask_path}")
    binary = sitk.GetImageFromArray((data > 0).astype(np.uint8))
    binary.CopyInformation(reference)
    sitk.WriteImage(binary, output_path)
    return {
        "path": output_path,
        "geometry_adjusted": geometry_adjusted,
        "original_geometry": original_geometry,
        "final_geometry": {
            "size": list(reference.GetSize()),
            "spacing": list(reference.GetSpacing()),
            "origin": list(reference.GetOrigin()),
            "direction": list(reference.GetDirection()),
        },
    }


async def download_and_pair_volumes(
    workspace_dir,
    resolved_pairs,
    download_volumes=True,
    task_name="segmentation_task",
    *,
    client: httpx.AsyncClient | None = None,
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
    async def trigger_download(url, task_name, uids_list):
        if not uids_list:
            return True
        logger.info(f"POST {url} | task={task_name} | uids={uids_list}")
        request_client = client or httpx.AsyncClient()
        try:
            response = await request_client.post(
                url,
                params={"task_name": task_name},
                json=uids_list,
                timeout=60,
            )
            logger.info("Trigger response: %s", response.status_code)
            if response.status_code != 200:
                logger.error("Trigger failed: %s", response.text[:300])
                return False
            return True
        except httpx.HTTPError:
            logger.exception("Download trigger failed")
            return False
        finally:
            if client is None:
                await request_client.aclose()

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

            def scan_exact_files():
                matches = {}
                expected_by_name = {
                    f"{str(search_key)}.nii.gz".casefold(): key
                    for key, search_key in uid_to_search_key.items()
                    if key in remaining_keys
                }
                for root, _, files in os.walk(search_dir):
                    for fname in files:
                        key = expected_by_name.get(fname.casefold())
                        if key is not None:
                            matches[key] = os.path.join(root, fname)
                return matches

            matches = await asyncio.to_thread(scan_exact_files)
            for key, full_path in matches.items():
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

    def copy_and_normalize_label(
        src_path,
        reference_volume_path,
        volume_uid,
        dest_dir,
        target_id=None,
        artifact_key=None,
    ):
        """Align the label to its reference volume and save a binary mask."""
        if artifact_key:
            filename = f"{artifact_key}.nii.gz"
        elif target_id:
            filename = f"{volume_uid}__{target_id}.nii.gz"
        else:
            filename = f"{volume_uid}.nii.gz"
        dst = os.path.join(dest_dir, filename)
        try:
            return align_and_binarize_mask(
                src_path,
                reference_volume_path,
                dst,
            )
        except Exception:
            logger.exception("Error normalizing label %s", src_path)
            return None

    # ------------------------------------------------------------------ #
    #  1. pair volumes and labels                                        #
    # ------------------------------------------------------------------ #
    volumes_to_process = []
    labels_to_process = []
    target_ids_to_process = []
    pair_ids_to_process = []
    artifact_keys_to_process = []

    for pair in resolved_pairs:
        volume_uid = str(pair.get("volume_uid") or "").strip()
        label_uid = str(pair.get("label_uid") or "").strip()
        target_id = str(pair.get("target_id") or "").strip()
        pair_id = str(pair.get("pair_id") or f"{volume_uid}__{target_id}").strip()
        artifact_key = str(pair.get("artifact_key") or pair_id).strip()
        if not volume_uid or not label_uid or not target_id:
            logger.error("Invalid resolved segmentation pair: %s", pair)
            continue
        volumes_to_process.append(volume_uid)
        labels_to_process.append(label_uid)
        target_ids_to_process.append(target_id)
        pair_ids_to_process.append(pair_id)
        artifact_keys_to_process.append(artifact_key)

    if not volumes_to_process:
        logger.error("Nothing to process; aborting.")
        return [], []

    logger.info(f"Pairs ready: {len(volumes_to_process)}")

    # ------------------------------------------------------------------ #
    #  2. trigger downloads (POST; MONAI saves to its local dirs)        #
    # ------------------------------------------------------------------ #
    if download_volumes:
        images_triggered = await trigger_download(
            DOWNLOAD_IMAGES_URL,
            TASK_NAME,
            list(dict.fromkeys(volumes_to_process)),
        )
        if not images_triggered:
            logger.error("Volume export request failed; aborting without polling")
            return [], [
                {"volume_uid": uid, "target_id": target_id}
                for uid, target_id in zip(volumes_to_process, target_ids_to_process)
            ]
    
    valid_labels = list(dict.fromkeys(uid for uid in labels_to_process if uid))
    labels_triggered = await trigger_download(
        DOWNLOAD_LABELS_URL,
        TASK_NAME,
        valid_labels,
    )
    if not labels_triggered:
        logger.error("Mask export request failed; aborting without polling")
        return [], [
            {"volume_uid": uid, "target_id": target_id}
            for uid, target_id in zip(volumes_to_process, target_ids_to_process)
        ]

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
    copied_volumes: dict[str, str] = {}
    for vol_uid, label_uid, target_id, pair_id, artifact_key in zip(
        volumes_to_process,
        labels_to_process,
        target_ids_to_process,
        pair_ids_to_process,
        artifact_keys_to_process,
    ):
        label_src = found_labels.get(label_uid) if label_uid else None
        if not label_src and label_uid:
            logger.error(
                f"Label file never appeared for volume={vol_uid} "
                f"(label_uid={label_uid}); skipping pair"
            )
            continue

        volume_path = os.path.join(FINAL_DIR_IMG, f"{vol_uid}.nii.gz")
        vol_result = volume_path
        if download_volumes:
            vol_src = found_volumes.get(vol_uid)
            if not vol_src:
                logger.error("Volume file never appeared for %s; skipping pair", vol_uid)
                continue
            if vol_uid not in copied_volumes:
                copied_volumes[vol_uid] = await asyncio.to_thread(
                    copy_volume,
                    vol_src,
                    vol_uid,
                    FINAL_DIR_IMG,
                )
            vol_result = copied_volumes[vol_uid]

        if label_src:
            label_result = await asyncio.to_thread(
                copy_and_normalize_label,
                label_src,
                vol_result,
                vol_uid,
                FINAL_DIR_LABEL,
                target_id,
                artifact_key,
            )
        else:
            label_result = True # No label to process for interactive manual

        if label_result == "SKIPPED_OOM":
            skipped_oom.append(
                {
                    "pair_id": pair_id,
                    "artifact_key": artifact_key,
                    "volume_uid": vol_uid,
                    "target_id": target_id,
                }
            )
            continue

        if vol_result and label_result:
            successful.append(
                {
                    "pair_id": pair_id,
                    "artifact_key": artifact_key,
                    "volume_uid": vol_uid,
                    "target_id": target_id,
                    "geometry_adjusted": (
                        bool(label_result.get("geometry_adjusted"))
                        if isinstance(label_result, dict)
                        else False
                    ),
                    "geometry": (
                        {
                            "original": label_result.get("original_geometry"),
                            "final": label_result.get("final_geometry"),
                        }
                        if isinstance(label_result, dict)
                        else None
                    ),
                }
            )
        else:
            logger.error("Failed to copy vol/label for %s", vol_uid)
            skipped_oom.append(
                {
                    "pair_id": pair_id,
                    "artifact_key": artifact_key,
                    "volume_uid": vol_uid,
                    "target_id": target_id,
                    "reason": "normalization_failed",
                }
            )

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
            pair_id = str(
                item.get("pair_id")
                or item.get("pairId")
                or (f"{uid}__{target_id}" if target_id else uid)
            )
            artifact_key = str(item.get("artifact_key") or pair_id)
        else:
            uid = str(item)
            target_id = ""
            target_label = None
            mask_path_override = ""
            pair_id = uid
            artifact_key = pair_id
        vol_path = os.path.join(volumes_dir, f"{uid}.nii.gz")
        default_mask_path = os.path.join(masks_dir, f"{artifact_key}.nii.gz")
        legacy_name_is_safe = (
            os.path.basename(pair_id) == pair_id
            and "/" not in pair_id
            and "\\" not in pair_id
        )
        legacy_mask_path = (
            os.path.join(masks_dir, f"{pair_id}.nii.gz")
            if legacy_name_is_safe
            else ""
        )
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
            elif (
                not os.path.exists(mask_path)
                and legacy_mask_path
                and os.path.exists(legacy_mask_path)
            ):
                mask_path = legacy_mask_path
        else:
            mask_path = (
                default_mask_path
                if os.path.exists(default_mask_path)
                else legacy_mask_path or default_mask_path
            )
        gif_filename = f"{artifact_key}.gif"
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
            volume_image = sitk.ReadImage(vol_path)
            mask_image = sitk.ReadImage(mask_path)
            if not _geometry_matches(mask_image, volume_image):
                align_and_binarize_mask(mask_path, vol_path, mask_path)
            vol_data = nib.load(vol_path).get_fdata()
            mask_data = nib.load(mask_path).get_fdata()

            if vol_data.shape != mask_data.shape:
                raise ValueError(
                    f"Mask alignment did not produce the reference shape for {uid}: "
                    f"volume={vol_data.shape}, mask={mask_data.shape}"
                )
            max_z = vol_data.shape[2]

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
                    "gifUrl": (
                        f"{os.environ.get('MAS_BASE_URL', 'http://localhost:8150').rstrip('/')}"
                        f"/files/{workspace_folder_name}/overlays/{gif_filename}"
                    ),
                    "gifPath": f"overlays/{gif_filename}",
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
def cleanup_discarded(
    workspace_dir,
    accepted_uids,
    modified_uids,
    artifact_keys_by_pair=None,
):
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
    artifact_keys_by_pair = artifact_keys_by_pair or {}
    mask_keys_to_keep = pair_keys_to_keep | {
        artifact_keys_by_pair[key]
        for key in pair_keys_to_keep
        if key in artifact_keys_by_pair
    }
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
                keep = pair_key in mask_keys_to_keep or volume_uid in pair_keys_to_keep
                if not keep:
                    os.remove(os.path.join(masks_dir, fname))
                    logger.info(f"Removed mask (discarded): {fname}")
