import httpx
import os
import logging
import json
import asyncio
from datetime import datetime
from typing import Annotated, Any, Dict
from pydantic import Field
import highdicom as hd
from pydicom.sr.codedict import codes
import numpy as np
from pathlib import Path
import io
import pydicom
import tempfile

from .utils import (
    parse_multipart_response,
    parse_nrrd_data,
    ensure_required_patient_tags
)

logger = logging.getLogger(__name__)

INFERENCE_TIMEOUT = httpx.Timeout(connect=30.0, read=1800.0, write=60.0, pool=30.0)
QUICK_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)

# Retry settings for transient server-disconnect errors (e.g. when monailabel
# is under heavy RAM pressure and drops the connection after finishing inference)
_MAX_INFERENCE_RETRIES = 3
_RETRY_BACKOFF_SECONDS = [5, 15, 30]   # wait before each retry attempt


class SegmentationService:

    def __init__(self, monai_url: str | None = None, orthanc_url: str | None = None):

        self.monai_url = monai_url or os.environ.get("MONAI_URL", "http://localhost:8000")
        self.orthanc_url = orthanc_url or os.environ.get("ORTHANC_URL", "http://localhost:8043")


    async def run_segmentation(
        self,
        model: Annotated[
            str,
            Field(description="MONAI Label model name to run inference with")
        ],
        series_instance_uid: Annotated[
            str,
            Field(description="DICOM SeriesInstanceUID to segment")
        ],
        label_info: Annotated[
            str,
            Field(description="Comma-separated anatomical labels to segment (e.g. 'spleen' or 'spleen,liver,kidney')")
        ],
        params: Annotated[
            Dict[str, Any] | None,
            Field(description="Optional inference parameters")
        ] = None,
    ):
        start_time = datetime.now()
        clean_model = model.strip().rstrip(",")

        frame_of_reference_uid = None
        study_instance_uid = None
        organ_list = self._normalize_label_info(label_info)

        # =========== get the study from the series UID ===========
        get_study_url = f"{self.monai_url}/volumes/{series_instance_uid}/study"
        async with httpx.AsyncClient(timeout=INFERENCE_TIMEOUT) as client:
            try:
                study_response = await client.get(get_study_url)
                if study_response.status_code == 200:
                    study_info = study_response.json()
                    study_instance_uid = study_info.get('study_uid')
                    logger.info(
                        "Retrieved StudyInstanceUID %s for SeriesInstanceUID %s",
                        study_instance_uid,
                        series_instance_uid,
                    )
                else:
                    logger.warning(
                        "Failed to retrieve study info for SeriesInstanceUID %s; status=%s",
                        series_instance_uid,
                        study_response.status_code,
                    )
            except Exception:
                logger.exception("Error retrieving study info for SeriesInstanceUID %s", series_instance_uid)

        # ====the inference using monai label=====

        infer_url = f"{self.monai_url}/infer/{clean_model}"
        query_params = {"image": series_instance_uid, "output": "all"}
        inference_params = dict(params or {})
        inference_params["result_extension"] = ".nrrd"
        inference_params["result_dtype"] = "uint8"
        inference_params["result_compress"] = False
        # NOTE: restore_label_idxs MUST be False for Voxtell.
        # Voxtell is a binary text-prompted model — it has no fixed label index map.
        # When True, MONAI's BasicInferTask remaps foreground pixel values (1) to
        # whatever index 'liver tumor' occupies in the label dict. Since that index
        # often resolves to 0 (background), the entire mask is silently erased.
        inference_params["restore_label_idxs"] = False

        files = {"params": (None, json.dumps(inference_params), "application/json")}

        # ---- Inference with retry on transient server-disconnect errors ----
        # The monailabel server can disconnect mid-response when RAM is near capacity
        # (97%+ RAM observed in logs). The inference itself finishes, but the HTTP
        # connection is dropped before the response arrives. A short backoff lets the
        # server free memory and accept the next request normally.
        inference_start = datetime.now()
        inference_duration = 0.0
        response = None
        last_exc = None

        for attempt in range(1, _MAX_INFERENCE_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=INFERENCE_TIMEOUT) as client:
                    response = await client.post(infer_url, params=query_params, files=files)
                inference_duration = (datetime.now() - inference_start).total_seconds()
                logger.info(
                    "Inference completed in %.1fs with status %s (attempt %s/%s)",
                    inference_duration,
                    response.status_code,
                    attempt,
                    _MAX_INFERENCE_RETRIES,
                )
                last_exc = None
                break  # success — exit retry loop

            except httpx.TimeoutException as e:
                elapsed = (datetime.now() - start_time).total_seconds()
                logger.error("Inference timeout after %.1fs: %s", elapsed, e)
                return {
                    "error": True,
                    "stage": "inference_timeout",
                    "message": f"Inference exceeded timeout after {elapsed:.1f}s. Try with smaller volume or different model.",
                    "elapsed_seconds": elapsed,
                }
            except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError) as e:
                last_exc = e
                elapsed = (datetime.now() - inference_start).total_seconds()
                if attempt < _MAX_INFERENCE_RETRIES:
                    wait = _RETRY_BACKOFF_SECONDS[attempt - 1]
                    logger.warning(
                        "Inference attempt %s/%s failed after %.1fs (%s: %s). "
                        "Server likely under memory pressure. Retrying in %ss...",
                        attempt,
                        _MAX_INFERENCE_RETRIES,
                        elapsed,
                        type(e).__name__,
                        e,
                        wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        "Inference failed after %s attempts: %s",
                        _MAX_INFERENCE_RETRIES,
                        e,
                    )
            except Exception as e:
                logger.exception("Inference request failed (attempt %s/%s)", attempt, _MAX_INFERENCE_RETRIES)
                return {"error": True, "stage": "inference_request", "message": str(e)}

        if last_exc is not None:
            # All retries exhausted for a transient connection error
            logger.exception("Inference request failed after all retries", exc_info=last_exc)
            return {"error": True, "stage": "inference_request", "message": str(last_exc)}

        result = {
            "endpoint": infer_url,
            "query_params": query_params,
            "sent_params": inference_params,
            "status_code": response.status_code,
            "inference_duration_seconds": inference_duration
        }

        if response.status_code != 200:
            result["error"] = True
            result["server_response"] = response.text[:500]
            logger.error("Inference failed with status %s: %s", response.status_code, response.text[:200])
            return result

        # ===== parsing the response into nrrd and centroids ============
        nrrd_data, centroids = parse_multipart_response(
            response.headers,
            response.content
        )
        logger.info(
            "Parsed multipart response: nrrd_data_size=%s bytes, centroids=%s",
            len(nrrd_data),
            list(centroids),
        )

        # ==== parse the nrrd and extract centroids ijk =====

        # parse the nrrd data into numpy array and header
        image, header = parse_nrrd_data(nrrd_data)
        raw_unique = np.unique(image)
        raw_max = float(image.max())
        raw_foreground = int((image > 0).sum())
        logger.info(
            "MONAI output: shape=%s unique=%s nrrd_sizes=%s space_directions=%s space_origin=%s",
            image.shape,
            raw_unique,
            header.get("sizes"),
            header.get("space directions"),
            header.get("space origin"),
        )
        logger.info(
            "[DEBUG] Raw pred stats: max=%.4f foreground_voxels=%s unique_values=%s",
            raw_max,
            raw_foreground,
            raw_unique.tolist(),
        )
        if raw_foreground == 0:
            logger.warning(
                "[EMPTY MASK] MONAI returned an all-zero mask for series %s. "
                "The model found no foreground voxels for prompt '%s'. "
                "This may indicate: (1) the organ/lesion is not visible in this scan, "
                "(2) the text prompt does not match any cached embedding, or "
                "(3) the sigmoid threshold (0.5) is too high for this volume.",
                series_instance_uid,
                label_info,
            )

        logger.info(
            "Parsed NRRD data: image_shape=%s, image_dtype=%s, header_keys=%s",
            image.shape,
            image.dtype,
            list(header.keys()),
        )
        labels = []
        async with httpx.AsyncClient(timeout=INFERENCE_TIMEOUT) as client:
            try:
                response_info = await client.get(f"{self.monai_url}/info/")
                if response_info.status_code == 200:
                    labels = response_info.json().get("labels", [])
                else:
                    logger.warning("MONAI Label /info/ returned %s", response_info.status_code)

            except Exception:
                logger.exception("Error fetching MONAI Label info")

        logger.info("Available labels in MONAI Label: %s", labels)

        # the centroid world and image coordinates

        centroids_ijk = {}

        for key, value in centroids.items():
            segment_index = int(value[0])           # same as parseInt(..., 10)
            image_coords = [float(v) for v in value[1:]]
            centroids_ijk[segment_index] = {
                "image": image_coords,
                "world": []
            }
        # ==== create the segmentation object =====

        segmentation_object = {
            "id": series_instance_uid,
            "label": f"Segmentation_{series_instance_uid}",
            "segments": [
                {
                    "segmentIndex": idx,
                    "label": label,
                    "isActive": True,
                    "isVisible": True
                }
                for idx, label in enumerate(labels, start=1)
            ],
            "isActive": True,
            "activeSegmentIndex": 1,
            "scalarData": image,
            "FrameOfReferenceUID": frame_of_reference_uid,
            "centroidsIJK": centroids_ijk
        }

        logger.info(
            "Constructed segmentation object with %s segments",
            len(segmentation_object["segments"]),
        )

        # ========== create pydicom datasets for the original series ============
        dicom_datasets = await self.fetch_source_datasets_from_orthanc(
            series_instance_uid=series_instance_uid
        )
        num_slices = len(dicom_datasets)
        rows = dicom_datasets[0].Rows
        cols = dicom_datasets[0].Columns
        logger.info("Source CT geometry: rows=%s cols=%s slices=%s", rows, cols, num_slices)

        # ========== inject missing tags due to de-identification (if any) ============
        ensure_required_patient_tags(dicom_datasets)
        # ============== create Algorithm description=================
        algorithm_identification = hd.AlgorithmIdentificationSequence(
                name='Automatic Segmentation',
                version='0.0',
                family=codes.cid7162.ArtificialIntelligence
            )
        # =========== create segmentation description =================
        labelmap_np: np.ndarray = segmentation_object["scalarData"]
        labelmap_np = labelmap_np.astype(np.uint8)
        # MONAI returns (1, X, Y, Z); squeeze the leading dim to get (X, Y, Z).
        if labelmap_np.ndim == 4 and labelmap_np.shape[0] == 1:
            labelmap_np = labelmap_np.squeeze(0)
        labelmap_np = np.transpose(labelmap_np, (2, 1, 0))
        logger.info("Labelmap shape after squeeze+transpose: %s", labelmap_np.shape)

        # VoxTell always returns binary masks — binarize with >0
        labelmap_np = (labelmap_np > 0).astype(np.uint8)

        logger.info(
            "After binarisation: unique=%s foreground_voxels=%s",
            np.unique(labelmap_np),
            int(labelmap_np.sum()),
        )
        description_segment = hd.seg.SegmentDescription(
            segment_number=1,
            segment_label=organ_list[0] if organ_list else "Segment",
            segmented_property_category=codes.cid7150.Tissue,
            segmented_property_type=codes.cid7166.ConnectiveTissue,
            algorithm_type=hd.seg.SegmentAlgorithmTypeValues.AUTOMATIC,
            algorithm_identification=algorithm_identification,
            tracking_uid=hd.UID(),
            tracking_id='Test Segmentation'
        )
        now = datetime.now()
        series_time = now.strftime("%I:%M:%S %p")

        logger.info(
            "DICOM-SEG input: source_type=%s is_dataset=%s ct_slices=%s mask_shape=%s",
            type(dicom_datasets[0]),
            isinstance(dicom_datasets[0], pydicom.dataset.Dataset),
            len(dicom_datasets),
            labelmap_np.shape,
        )

        seg = hd.seg.Segmentation(
                source_images=dicom_datasets,  # references + geometry
                pixel_array=labelmap_np,
                segmentation_type=hd.seg.SegmentationTypeValues.BINARY,
                segment_descriptions=[description_segment],
                series_instance_uid=hd.UID(),
                series_number=1001,
                sop_instance_uid=hd.UID(),
                series_description=f"{organ_list[0] if organ_list else 'Segment'}-{clean_model}-{series_time}-g",
                instance_number=1,
                manufacturer='Manufacturer Name',
                manufacturer_model_name='MONAI Label Backend',
                software_versions='-',
                device_serial_number='XYZ',
                omit_empty_frames=False,
            )
        referenced_frames = (
            len(seg.PerFrameFunctionalGroupsSequence)
            if hasattr(seg, "PerFrameFunctionalGroupsSequence")
            else None
        )
        logger.info(
            "Highdicom segmentation: frames=%s referenced_frames=%s",
            seg.NumberOfFrames,
            referenced_frames,
        )

        logger.info(f"Created DICOM-SEG object with SeriesInstanceUID: {seg.SeriesInstanceUID}")

        # ========== saving to orthanc ====================

        seg_bytes_io = io.BytesIO()
        seg.save_as(seg_bytes_io)
        seg_bytes_io.seek(0)
        async with httpx.AsyncClient(timeout=INFERENCE_TIMEOUT) as client:
            headers = {"Content-Type": "application/dicom"}
            response = await client.post(f"{self.orthanc_url}/instances", headers=headers, content=seg_bytes_io.read())

            if response.status_code in (200, 201):
                instance_id = response.json()["ID"]
                logger.info(f"DICOM-SEG successfully uploaded to Orthanc. InstanceID: {instance_id}")
                await self._sync_uploaded_segmentation_series(instance_id, seg.SeriesInstanceUID)
            else:
                logger.error("Failed to upload DICOM-SEG: %s %s", response.status_code, response.text)
                result["upload_error"] = True
                result["upload_status_code"] = response.status_code
                result["upload_response"] = response.text[:500]

        return result

    async def _sync_uploaded_segmentation_series(self, instance_id: str, dicom_series_uid: str) -> None:
        """
        Proactively sync the newly uploaded SEG instead of waiting for Orthanc's
        Lua stable-series webhook. The Lua hook can be delayed or time out when
        MONAI is busy with the next large inference.
        """
        try:
            async with httpx.AsyncClient(timeout=QUICK_TIMEOUT) as client:
                instance_response = await client.get(f"{self.orthanc_url}/instances/{instance_id}")
                if instance_response.status_code != 200:
                    logger.warning(
                        "Could not resolve uploaded SEG instance %s for direct sync: status=%s",
                        instance_id,
                        instance_response.status_code,
                    )
                    return

                orthanc_series_id = instance_response.json().get("ParentSeries")
                if not orthanc_series_id:
                    logger.warning("Uploaded SEG instance %s has no ParentSeries.", instance_id)
                    return

                sync_response = await client.post(
                    f"{self.monai_url}/sync/orthanc-webhook",
                    json={
                        "change_type": "stable_series",
                        "resource_id": orthanc_series_id,
                        "level": "series",
                        "modality": "SEG",
                        "series_uid": str(dicom_series_uid),
                    },
                )
                if sync_response.status_code == 200:
                    logger.info(
                        "Direct Orthanc sync accepted for uploaded SEG series: orthanc_series_id=%s",
                        orthanc_series_id,
                    )
                else:
                    logger.warning(
                        "Direct Orthanc sync failed for uploaded SEG series %s: status=%s body=%s",
                        orthanc_series_id,
                        sync_response.status_code,
                        sync_response.text[:200],
                    )
        except Exception as exc:
            logger.warning("Direct Orthanc sync failed for uploaded SEG instance %s: %s", instance_id, exc)

    @staticmethod
    def _normalize_label_info(label_info) -> list[str]:
        if isinstance(label_info, list):
            return [str(label).strip().lower() for label in label_info if str(label).strip()]
        if isinstance(label_info, str):
            return [label.strip().lower() for label in label_info.split(",") if label.strip()]
        return ["segmentation"]

    async def fetch_source_datasets_from_orthanc(
        self,
        series_instance_uid,
        timeout: float = 300.0
    ) -> list[pydicom.Dataset]:
        """
        Fetch all DICOM instances of a series using Orthanc REST API.
        Returns list of pydicom.Dataset objects.
        """
        timeout_config = httpx.Timeout(timeout)

        async with httpx.AsyncClient(timeout=timeout_config) as client:
            logger.info("Searching series by SeriesInstanceUID: %s", series_instance_uid)

            # find the internal series id
            find_payload = {
                "Level": "Series",
                "Query": {"SeriesInstanceUID": series_instance_uid}
            }
            response = await client.post(f"{self.orthanc_url}/tools/find", json=find_payload)
            response.raise_for_status()
            results = response.json()

            if not results:
                raise ValueError(f"Series with SeriesInstanceUID {series_instance_uid} not found in Orthanc")

            series_id = results[0]
            logger.info("Resolved Orthanc series_id: %s", series_id)

            # get list of Instance IDs
            series_response = await client.get(f"{self.orthanc_url}/series/{series_id}")
            series_response.raise_for_status()
            series_data = series_response.json()
            instance_ids = series_data.get("Instances", [])

            if not instance_ids:
                raise ValueError(f"No instances found in series {series_instance_uid}")

            logger.info("Found %s instances in series %s", len(instance_ids), series_instance_uid)

            # download each instance file and parse to pydicom.Dataset
            dicom_paths = []
            with tempfile.TemporaryDirectory(prefix="orthanc-dicom-") as temp_dir_name:
                temp_dir = Path(temp_dir_name)
                for i, inst_id in enumerate(instance_ids):
                    file_url = f"{self.orthanc_url}/instances/{inst_id}/file"
                    file_response = await client.get(file_url)
                    file_response.raise_for_status()

                    # save to tmp
                    temp_path = temp_dir / f"instance_{i:06d}.dcm"
                    temp_path.write_bytes(file_response.content)

                    # Load with pydicom to get InstanceNumber for sorting
                    try:
                        ds = pydicom.dcmread(str(temp_path))
                        dicom_paths.append((ds, temp_path))
                        logger.debug("Saved and read %s -> %s", inst_id, temp_path)
                    except Exception as e:
                        logger.warning("Failed pydicom read for %s: %s", inst_id, e)
                        temp_path.unlink(missing_ok=True)
                        continue

                if not dicom_paths:
                    raise ValueError("No valid DICOM files loaded")

                # sort by InstanceNumber (0020,0013), default to 0 if missing
                dicoms_sorted = sorted(
                    dicom_paths,
                    key=lambda x: int(x[0].get((0x0020, 0x0013), pydicom.DataElement((0x0020, 0x0013), 'IS', '0')).value or 0)
                )

                source_images = [ds for ds, _ in dicoms_sorted]
                logger.info("Loaded and sorted %s pydicom datasets", len(source_images))
                return source_images
