import httpx
import os
import logging
import json
import asyncio
from datetime import datetime
from functools import partial
from typing import Annotated, Any, Dict, NotRequired, TypedDict
from pydantic import Field
import highdicom as hd
from pydicom.sr.codedict import codes
import numpy as np
import io
import pydicom
from scipy import ndimage

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


class InferenceResult(TypedDict):
    error: bool
    stage: str
    message: str
    volume_uid: str
    model: str
    prompt: str | None
    status_code: NotRequired[int]
    inference_duration_seconds: NotRequired[float]
    segmentation_series_uid: NotRequired[str]
    orthanc_instance_id: NotRequired[str]
    sync_succeeded: NotRequired[bool]
    geometry_adjusted: NotRequired[bool]
    original_mask_shape: NotRequired[list[int]]
    final_mask_shape: NotRequired[list[int]]
    elapsed_seconds: NotRequired[float]
    endpoint: NotRequired[str]
    query_params: NotRequired[dict[str, Any]]
    sent_params: NotRequired[dict[str, Any]]
    server_response: NotRequired[str]
    upload_error: NotRequired[bool]
    upload_status_code: NotRequired[int]
    upload_response: NotRequired[str]
    retry_count: NotRequired[int]


class SegmentationService:

    def __init__(
        self,
        monai_url: str | None = None,
        orthanc_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):

        self.monai_url = monai_url or os.environ.get("MONAI_URL", "http://localhost:8000")
        self.orthanc_url = orthanc_url or os.environ.get("ORTHANC_URL", "http://localhost:8043")
        self.client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


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
    ) -> InferenceResult:
        try:
            return await self._run_segmentation(
                model=model,
                series_instance_uid=series_instance_uid,
                label_info=label_info,
                params=params,
            )
        except Exception as exc:
            logger.exception(
                "Segmentation processing failed for volume=%s model=%s",
                series_instance_uid,
                model,
            )
            return {
                "error": True,
                "stage": "processing_exception",
                "message": str(exc),
                "volume_uid": series_instance_uid,
                "model": model.strip().rstrip(","),
                "prompt": self._first_text_prompt(params or {}),
            }

    async def _run_segmentation(
        self,
        model: str,
        series_instance_uid: str,
        label_info: str,
        params: Dict[str, Any] | None = None,
    ) -> InferenceResult:
        start_time = datetime.now()
        clean_model = model.strip().rstrip(",")

        organ_list = self._normalize_label_info(label_info)

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
                response = await self.client.post(
                    infer_url,
                    params=query_params,
                    files=files,
                    timeout=INFERENCE_TIMEOUT,
                )
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
                    "message": (
                        f"Inference exceeded timeout after {elapsed:.1f}s. "
                        "Try with smaller volume or different model."
                    ),
                    "elapsed_seconds": elapsed,
                    "volume_uid": series_instance_uid,
                    "model": clean_model,
                    "prompt": self._first_text_prompt(inference_params),
                    "retry_count": attempt,
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
                return {
                    "error": True,
                    "stage": "inference_request",
                    "message": str(e),
                    "volume_uid": series_instance_uid,
                    "model": clean_model,
                    "prompt": self._first_text_prompt(inference_params),
                    "retry_count": attempt,
                }

        if last_exc is not None:
            # All retries exhausted for a transient connection error
            logger.exception("Inference request failed after all retries", exc_info=last_exc)
            return {
                "error": True,
                "stage": "inference_request",
                "message": str(last_exc),
                "volume_uid": series_instance_uid,
                "model": clean_model,
                "prompt": self._first_text_prompt(inference_params),
                "retry_count": _MAX_INFERENCE_RETRIES,
            }

        result: InferenceResult = {
            "error": False,
            "stage": "completed",
            "message": "Segmentation completed",
            "volume_uid": series_instance_uid,
            "model": clean_model,
            "prompt": self._first_text_prompt(inference_params),
            "endpoint": infer_url,
            "query_params": query_params,
            "sent_params": inference_params,
            "status_code": response.status_code,
            "inference_duration_seconds": inference_duration,
            "retry_count": attempt,
        }

        if response.status_code != 200:
            result["error"] = True
            result["stage"] = "inference_response"
            result["message"] = f"MONAI inference returned HTTP {response.status_code}"
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
        image, header = await asyncio.to_thread(parse_nrrd_data, nrrd_data)
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
                name=clean_model,
                version='unknown',
                family=codes.cid7162.ArtificialIntelligence
            )
        # =========== create segmentation description =================
        labelmap_np = image.astype(np.uint8)
        # MONAI returns (1, X, Y, Z); squeeze the leading dim to get (X, Y, Z).
        if labelmap_np.ndim == 4 and labelmap_np.shape[0] == 1:
            labelmap_np = labelmap_np.squeeze(0)
        labelmap_np = np.transpose(labelmap_np, (2, 1, 0))
        logger.info("Labelmap shape after squeeze+transpose: %s", labelmap_np.shape)

        # VoxTell always returns binary masks — binarize with >0
        labelmap_np = (labelmap_np > 0).astype(np.uint8)
        expected_shape = (num_slices, int(rows), int(cols))
        original_shape = tuple(int(value) for value in labelmap_np.shape)
        geometry_adjusted = original_shape != expected_shape
        if geometry_adjusted:
            zoom = tuple(
                target / source
                for source, target in zip(original_shape, expected_shape)
            )
            labelmap_np = await asyncio.to_thread(
                ndimage.zoom,
                labelmap_np,
                zoom,
                order=0,
                prefilter=False,
            )
            labelmap_np = (labelmap_np > 0).astype(np.uint8)
            logger.warning(
                "Resampled mask with nearest-neighbor interpolation from %s "
                "to source DICOM shape %s",
                original_shape,
                expected_shape,
            )
        result["geometry_adjusted"] = geometry_adjusted
        result["original_mask_shape"] = list(original_shape)
        result["final_mask_shape"] = list(labelmap_np.shape)

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
        target_label = organ_list[0] if organ_list else "Segment"

        logger.info(
            "DICOM-SEG input: source_type=%s is_dataset=%s ct_slices=%s mask_shape=%s",
            type(dicom_datasets[0]),
            isinstance(dicom_datasets[0], pydicom.dataset.Dataset),
            len(dicom_datasets),
            labelmap_np.shape,
        )

        seg = await asyncio.to_thread(
            partial(
                hd.seg.Segmentation,
                source_images=dicom_datasets,  # references + geometry
                pixel_array=labelmap_np,
                segmentation_type=hd.seg.SegmentationTypeValues.BINARY,
                segment_descriptions=[description_segment],
                series_instance_uid=hd.UID(),
                series_number=1001,
                sop_instance_uid=hd.UID(),
                # Target identity lives in SegmentLabel/target_label. Keep the
                # human-readable SeriesDescription free of encoded model/time
                # fields so no runtime code is tempted to parse provenance.
                series_description=f"Segmentation: {target_label}",
                instance_number=1,
                manufacturer='Manufacturer Name',
                manufacturer_model_name='MONAI Label Backend',
                software_versions='-',
                device_serial_number='XYZ',
                omit_empty_frames=False,
            )
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

        seg_uid = str(seg.SeriesInstanceUID)
        result["segmentation_series_uid"] = seg_uid
        seg_bytes_io = io.BytesIO()
        await asyncio.to_thread(seg.save_as, seg_bytes_io)
        seg_bytes_io.seek(0)
        headers = {"Content-Type": "application/dicom"}
        response = await self.client.post(
            f"{self.orthanc_url}/instances",
            headers=headers,
            content=seg_bytes_io.read(),
            timeout=INFERENCE_TIMEOUT,
        )

        if response.status_code in (200, 201):
            instance_id = response.json()["ID"]
            result["orthanc_instance_id"] = instance_id
            logger.info(
                "DICOM-SEG successfully uploaded to Orthanc. InstanceID: %s",
                instance_id,
            )
            result["sync_succeeded"] = await self._sync_uploaded_segmentation_series(
                instance_id,
                seg_uid,
            )
        else:
            logger.error("Failed to upload DICOM-SEG: %s %s", response.status_code, response.text)
            result["error"] = True
            result["stage"] = "orthanc_upload"
            result["message"] = f"Orthanc upload returned HTTP {response.status_code}"
            result["upload_error"] = True
            result["upload_status_code"] = response.status_code
            result["upload_response"] = response.text[:500]

        return result

    async def _sync_uploaded_segmentation_series(
        self,
        instance_id: str,
        dicom_series_uid: str,
    ) -> bool:
        """
        Proactively sync the newly uploaded SEG instead of waiting for Orthanc's
        Lua stable-series webhook. The Lua hook can be delayed or time out when
        MONAI is busy with the next large inference.
        """
        try:
            instance_response = await self.client.get(
                f"{self.orthanc_url}/instances/{instance_id}",
                timeout=QUICK_TIMEOUT,
            )
            if instance_response.status_code != 200:
                logger.warning(
                    "Could not resolve uploaded SEG instance %s for direct sync: status=%s",
                    instance_id,
                    instance_response.status_code,
                )
                return False

            orthanc_series_id = instance_response.json().get("ParentSeries")
            if not orthanc_series_id:
                logger.warning("Uploaded SEG instance %s has no ParentSeries.", instance_id)
                return False

            sync_response = await self.client.post(
                f"{self.monai_url}/sync/orthanc-webhook",
                json={
                    "change_type": "stable_series",
                    "resource_id": orthanc_series_id,
                    "level": "series",
                    "modality": "SEG",
                    "series_uid": str(dicom_series_uid),
                },
                timeout=QUICK_TIMEOUT,
            )
            if sync_response.status_code == 200:
                logger.info(
                    "Direct Orthanc sync accepted for uploaded SEG series: orthanc_series_id=%s",
                    orthanc_series_id,
                )
                return True
            logger.warning(
                "Direct Orthanc sync failed for uploaded SEG series %s: status=%s body=%s",
                orthanc_series_id,
                sync_response.status_code,
                sync_response.text[:200],
            )
            return False
        except Exception as exc:
            logger.warning("Direct Orthanc sync failed for uploaded SEG instance %s: %s", instance_id, exc)
            return False

    @staticmethod
    def _normalize_label_info(label_info) -> list[str]:
        if isinstance(label_info, list):
            return [str(label).strip().lower() for label in label_info if str(label).strip()]
        if isinstance(label_info, str):
            return [label.strip().lower() for label in label_info.split(",") if label.strip()]
        return ["segmentation"]

    @staticmethod
    def _first_text_prompt(params: dict[str, Any]) -> str | None:
        prompts = params.get("text_prompts")
        if isinstance(prompts, list) and prompts:
            return str(prompts[0])
        if isinstance(prompts, str):
            return prompts
        return None

    @staticmethod
    def _physical_slice_position(dataset: pydicom.Dataset) -> float | None:
        try:
            orientation = np.asarray(dataset.ImageOrientationPatient, dtype=float)
            position = np.asarray(dataset.ImagePositionPatient, dtype=float)
            if orientation.size != 6 or position.size != 3:
                return None
            normal = np.cross(orientation[:3], orientation[3:])
            if np.linalg.norm(normal) == 0:
                return None
            return float(np.dot(position, normal))
        except (AttributeError, TypeError, ValueError):
            return None

    @classmethod
    def _sort_source_datasets(
        cls,
        datasets: list[pydicom.Dataset],
    ) -> list[pydicom.Dataset]:
        if not datasets:
            raise ValueError("Cannot sort an empty DICOM series")
        expected_rows = int(datasets[0].Rows)
        expected_columns = int(datasets[0].Columns)
        for dataset in datasets:
            if (
                int(dataset.Rows) != expected_rows
                or int(dataset.Columns) != expected_columns
            ):
                raise ValueError("DICOM source frames have inconsistent dimensions")

        physical_positions = [cls._physical_slice_position(ds) for ds in datasets]
        if all(value is not None for value in physical_positions):
            reference_orientation = np.asarray(
                datasets[0].ImageOrientationPatient,
                dtype=float,
            )
            for dataset in datasets[1:]:
                orientation = np.asarray(
                    dataset.ImageOrientationPatient,
                    dtype=float,
                )
                if not np.allclose(
                    orientation,
                    reference_orientation,
                    rtol=1e-5,
                    atol=1e-5,
                ):
                    raise ValueError(
                        "DICOM source frames have inconsistent orientations"
                    )
            paired = sorted(
                zip(physical_positions, datasets),
                key=lambda item: float(item[0]),
            )
            sorted_positions = [float(item[0]) for item in paired]
            if len(sorted_positions) > 1:
                spacing = np.diff(sorted_positions)
                if np.any(spacing <= 0):
                    raise ValueError("DICOM slices have duplicate physical positions")
                if not np.allclose(spacing, np.median(spacing), rtol=0.05, atol=1e-3):
                    logger.warning(
                        "Source DICOM slice spacing is non-uniform: %s",
                        spacing.tolist(),
                    )
            return [dataset for _, dataset in paired]

        logger.warning(
            "ImagePositionPatient/ImageOrientationPatient unavailable; "
            "falling back to InstanceNumber ordering"
        )
        return sorted(
            datasets,
            key=lambda ds: int(getattr(ds, "InstanceNumber", 0) or 0),
        )

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
        logger.info("Searching series by SeriesInstanceUID: %s", series_instance_uid)

        find_payload = {
            "Level": "Series",
            "Query": {"SeriesInstanceUID": series_instance_uid},
        }
        response = await self.client.post(
            f"{self.orthanc_url}/tools/find",
            json=find_payload,
            timeout=timeout_config,
        )
        response.raise_for_status()
        results = response.json()
        if not results:
            raise ValueError(
                f"Series with SeriesInstanceUID {series_instance_uid} not found in Orthanc"
            )

        series_id = results[0]
        series_response = await self.client.get(
            f"{self.orthanc_url}/series/{series_id}",
            timeout=timeout_config,
        )
        series_response.raise_for_status()
        instance_ids = series_response.json().get("Instances", [])
        if not instance_ids:
            raise ValueError(f"No instances found in series {series_instance_uid}")

        datasets: list[pydicom.Dataset] = []
        for instance_id in instance_ids:
            file_response = await self.client.get(
                f"{self.orthanc_url}/instances/{instance_id}/file",
                timeout=timeout_config,
            )
            file_response.raise_for_status()
            try:
                dataset = await asyncio.to_thread(
                    pydicom.dcmread,
                    io.BytesIO(file_response.content),
                )
                datasets.append(dataset)
            except Exception as exc:
                logger.warning("Failed pydicom read for %s: %s", instance_id, exc)

        if not datasets:
            raise ValueError("No valid DICOM files loaded")
        source_images = self._sort_source_datasets(datasets)
        logger.info(
            "Loaded and physically sorted %s pydicom datasets",
            len(source_images),
        )
        return source_images
