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
from pydicom.uid import generate_uid
from scipy import ndimage

from services.timeout_policy import (
    CONTROL_HTTP_TIMEOUT,
    SEGMENTATION_JOB_TIMEOUT_SECONDS,
    SEGMENTATION_POLL_SECONDS,
    SEGMENTATION_RESULT_READ_SECONDS,
    SEGMENTATION_UNAVAILABLE_GRACE_SECONDS,
)

from .utils import (
    parse_multipart_response,
    parse_nrrd_data,
    ensure_required_patient_tags
)
from .naming import build_segmentation_series_description

logger = logging.getLogger(__name__)

INFERENCE_TIMEOUT = httpx.Timeout(
    connect=30.0,
    read=SEGMENTATION_RESULT_READ_SECONDS,
    write=60.0,
    pool=30.0,
)
QUICK_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


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
    inference_job_id: NotRequired[str]
    is_empty_mask: NotRequired[bool]
    foreground_voxels: NotRequired[int]
    z_axis_reversed: NotRequired[bool]
    slice_alignment: NotRequired[dict[str, Any]]


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

    @staticmethod
    def _fallback_job_id(
        model: str,
        series_instance_uid: str,
        params: dict[str, Any],
    ) -> str:
        import hashlib

        encoded = json.dumps(
            {
                "model": model,
                "image": series_instance_uid,
                "params": params,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def _cancel_inference_job(self, job_id: str) -> None:
        try:
            await self.client.delete(
                f"{self.monai_url}/inference-jobs/{job_id}",
                timeout=CONTROL_HTTP_TIMEOUT,
            )
        except Exception as exc:
            logger.warning("Could not cancel inference job %s: %s", job_id, exc)

    async def purge_inference_job(self, job_id: str | None) -> None:
        if not job_id:
            return
        try:
            await self.client.delete(
                f"{self.monai_url}/inference-jobs/{job_id}",
                params={"purge": "true"},
                timeout=CONTROL_HTTP_TIMEOUT,
            )
        except Exception as exc:
            logger.warning("Could not purge inference job %s: %s", job_id, exc)

    async def _await_inference_job(
        self,
        *,
        job_id: str,
        model: str,
        series_instance_uid: str,
        params: dict[str, Any],
    ) -> tuple[httpx.Response | None, dict[str, Any]]:
        job_url = f"{self.monai_url}/inference-jobs/{job_id}"
        request_payload = {
            "job_id": job_id,
            "model": model,
            "image": series_instance_uid,
            "params": params,
            "max_runtime_seconds": SEGMENTATION_JOB_TIMEOUT_SECONDS,
        }
        started = asyncio.get_running_loop().time()
        unavailable_since: float | None = None
        start_confirmed = False

        try:
            while True:
                elapsed = asyncio.get_running_loop().time() - started
                if elapsed >= SEGMENTATION_JOB_TIMEOUT_SECONDS:
                    await self._cancel_inference_job(job_id)
                    return None, {
                        "status": "TIMED_OUT",
                        "error": (
                            f"Inference job exceeded "
                            f"{SEGMENTATION_JOB_TIMEOUT_SECONDS:.0f}s"
                        ),
                    }

                if not start_confirmed:
                    try:
                        start_response = await self.client.post(
                            f"{self.monai_url}/inference-jobs/",
                            json=request_payload,
                            timeout=QUICK_TIMEOUT,
                        )
                        start_response.raise_for_status()
                        start_confirmed = True
                        unavailable_since = None
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code < 500:
                            return None, {
                                "status": "FAILED",
                                "error": (
                                    "Inference job could not be started: "
                                    f"HTTP {exc.response.status_code} "
                                    f"{exc.response.text[:300]}"
                                ),
                            }
                        now = asyncio.get_running_loop().time()
                        unavailable_since = unavailable_since or now
                    except (
                        httpx.ConnectError,
                        httpx.ReadError,
                        httpx.TimeoutException,
                        httpx.RemoteProtocolError,
                    ) as exc:
                        now = asyncio.get_running_loop().time()
                        unavailable_since = unavailable_since or now

                    if not start_confirmed:
                        if (
                            unavailable_since is not None
                            and asyncio.get_running_loop().time() - unavailable_since
                            >= SEGMENTATION_UNAVAILABLE_GRACE_SECONDS
                        ):
                            logger.warning(
                                "Inference service remained unavailable while "
                                "starting the job for %ss. Continuing to wait...",
                                SEGMENTATION_UNAVAILABLE_GRACE_SECONDS
                            )
                        await asyncio.sleep(SEGMENTATION_POLL_SECONDS)
                        continue

                try:
                    status_response = await self.client.get(
                        job_url,
                        timeout=CONTROL_HTTP_TIMEOUT,
                    )
                    status_response.raise_for_status()
                    job = status_response.json()
                    unavailable_since = None
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        start_confirmed = False
                        await asyncio.sleep(SEGMENTATION_POLL_SECONDS)
                        continue
                    if exc.response.status_code < 500:
                        return None, {
                            "status": "FAILED",
                            "error": (
                                "Inference job status failed: "
                                f"HTTP {exc.response.status_code} "
                                f"{exc.response.text[:300]}"
                            ),
                        }
                    now = asyncio.get_running_loop().time()
                    unavailable_since = unavailable_since or now
                    if now - unavailable_since >= SEGMENTATION_UNAVAILABLE_GRACE_SECONDS:
                        logger.warning(
                            "Inference service remained unavailable for %ss: %s. Continuing to wait...",
                            SEGMENTATION_UNAVAILABLE_GRACE_SECONDS,
                            exc
                        )
                    await asyncio.sleep(SEGMENTATION_POLL_SECONDS)
                    continue
                except (
                    httpx.ConnectError,
                    httpx.ReadError,
                    httpx.TimeoutException,
                    httpx.RemoteProtocolError,
                ) as exc:
                    now = asyncio.get_running_loop().time()
                    unavailable_since = unavailable_since or now
                    if now - unavailable_since >= SEGMENTATION_UNAVAILABLE_GRACE_SECONDS:
                        logger.warning(
                            "Inference service remained unavailable for %ss: %s. Continuing to wait...",
                            SEGMENTATION_UNAVAILABLE_GRACE_SECONDS,
                            exc
                        )
                    await asyncio.sleep(SEGMENTATION_POLL_SECONDS)
                    continue

                job_status = str(job.get("status") or "").upper()
                if job_status == "SUCCEEDED":
                    result_timeout = httpx.Timeout(
                        connect=15.0,
                        read=SEGMENTATION_RESULT_READ_SECONDS,
                        write=30.0,
                        pool=15.0,
                    )
                    result_response = await self.client.get(
                        f"{job_url}/result",
                        timeout=result_timeout,
                    )
                    result_response.raise_for_status()
                    return result_response, job
                if job_status in {"FAILED", "TIMED_OUT", "CANCELLED"}:
                    return None, job

                await asyncio.sleep(SEGMENTATION_POLL_SECONDS)
        except asyncio.CancelledError:
            try:
                await asyncio.wait_for(
                    self._cancel_inference_job(job_id),
                    timeout=5.0,
                )
            except Exception:
                pass
            raise


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
        job_id: str | None = None,
    ) -> InferenceResult:
        try:
            return await self._run_segmentation(
                model=model,
                series_instance_uid=series_instance_uid,
                label_info=label_info,
                params=params,
                job_id=job_id,
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
        job_id: str | None = None,
    ) -> InferenceResult:
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

        effective_job_id = job_id or self._fallback_job_id(
            clean_model,
            series_instance_uid,
            inference_params,
        )

        # Start or reattach to the deterministic durable inference job. The
        # short requests below never hold this worker on one long HTTP read.
        inference_start = datetime.now()
        response, job = await self._await_inference_job(
            job_id=effective_job_id,
            model=clean_model,
            series_instance_uid=series_instance_uid,
            params=inference_params,
        )
        inference_duration = (datetime.now() - inference_start).total_seconds()
        attempt = int(job.get("attempt") or 1)

        if response is None:
            job_status = str(job.get("status") or "FAILED").lower()
            return {
                "error": True,
                "stage": (
                    "inference_timeout"
                    if job_status == "timed_out"
                    else "inference_request"
                ),
                "message": str(job.get("error") or f"Inference job {job_status}"),
                "elapsed_seconds": inference_duration,
                "volume_uid": series_instance_uid,
                "model": clean_model,
                "prompt": self._first_text_prompt(inference_params),
                "retry_count": attempt,
                "inference_job_id": effective_job_id,
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
            "inference_job_id": effective_job_id,
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
            result.update(
                {
                    "is_empty_mask": True,
                    "foreground_voxels": 0,
                    "message": (
                        "Segmentation completed with no foreground; the source "
                        "volume will be shown without a mask"
                    ),
                }
            )
            # An all-zero prediction is a valid model result.  Do not try to
            # encode it as DICOM-SEG: an empty SEG is not useful downstream and
            # some DICOM writers reject pixel arrays with no encoded segment.
            return result

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

        labelmap_np, z_axis_reversed, slice_alignment = (
            self._align_labelmap_z_to_source_slices(
                labelmap_np,
                header,
                dicom_datasets,
            )
        )
        result["z_axis_reversed"] = z_axis_reversed
        result["slice_alignment"] = slice_alignment

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

        foreground_voxels = int(labelmap_np.sum())
        logger.info(
            "After binarisation: unique=%s foreground_voxels=%s",
            np.unique(labelmap_np),
            foreground_voxels,
        )
        if foreground_voxels == 0:
            result.update(
                {
                    "is_empty_mask": True,
                    "foreground_voxels": 0,
                    "message": (
                        "Segmentation completed with no foreground after geometry "
                        "normalization; the source volume will be shown without a mask"
                    ),
                }
            )
            return result
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

        uid_prefix = "1.2.826.0.1.3680043.10.511.3."
        seg_series_uid = generate_uid(
            prefix=uid_prefix,
            entropy_srcs=[effective_job_id, "series"],
        )
        seg_sop_uid = generate_uid(
            prefix=uid_prefix,
            entropy_srcs=[effective_job_id, "instance"],
        )

        existing_instance_id = await self._find_existing_segmentation_instance(
            seg_series_uid
        )
        if existing_instance_id:
            result["segmentation_series_uid"] = seg_series_uid
            result["orthanc_instance_id"] = existing_instance_id
            result["sync_succeeded"] = await self._sync_uploaded_segmentation_series(
                existing_instance_id,
                seg_series_uid,
            )
            logger.info(
                "Reconciled existing DICOM-SEG for inference job %s: %s",
                effective_job_id,
                seg_series_uid,
            )
            return result

        seg = await asyncio.to_thread(
            partial(
                hd.seg.Segmentation,
                source_images=dicom_datasets,  # references + geometry
                pixel_array=labelmap_np,
                segmentation_type=hd.seg.SegmentationTypeValues.BINARY,
                segment_descriptions=[description_segment],
                series_instance_uid=seg_series_uid,
                series_number=1001,
                sop_instance_uid=seg_sop_uid,
                series_description=build_segmentation_series_description(
                    clean_model,
                    target_label,
                ),
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

    async def _find_existing_segmentation_instance(
        self,
        dicom_series_uid: str,
    ) -> str | None:
        """Reconcile a deterministic SEG UID before attempting another upload."""
        try:
            find_response = await self.client.post(
                f"{self.orthanc_url}/tools/find",
                json={
                    "Level": "Series",
                    "Query": {"SeriesInstanceUID": dicom_series_uid},
                },
                timeout=QUICK_TIMEOUT,
            )
            find_response.raise_for_status()
            series_ids = find_response.json()
            if not series_ids:
                return None

            series_response = await self.client.get(
                f"{self.orthanc_url}/series/{series_ids[0]}",
                timeout=QUICK_TIMEOUT,
            )
            series_response.raise_for_status()
            instances = series_response.json().get("Instances") or []
            return str(instances[0]) if instances else None
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Could not reconcile DICOM-SEG series %s: %s",
                dicom_series_uid,
                exc,
            )
            return None

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

    @staticmethod
    def _nrrd_space_to_lps_signs(space: Any) -> np.ndarray:
        """Return component signs that convert an NRRD world space to DICOM LPS.

        NRRD commonly uses either LPS (left-posterior-superior) or RAS
        (right-anterior-superior).  DICOM ImagePositionPatient and
        ImageOrientationPatient are always expressed in LPS.
        """
        normalized = str(space or "").strip().lower().replace("_", "-")
        aliases = {
            "lps": "left-posterior-superior",
            "ras": "right-anterior-superior",
            "las": "left-anterior-superior",
            "rps": "right-posterior-superior",
        }
        normalized = aliases.get(normalized, normalized)
        parts = [part for part in normalized.split("-") if part]
        if len(parts) < 3:
            # pynrrd files produced by the MONAI/SimpleITK path are normally
            # LPS.  Keep that as the conservative default, but log it.
            logger.warning(
                "NRRD space %r is missing or unsupported; assuming DICOM LPS",
                space,
            )
            return np.ones(3, dtype=float)

        signs = []
        for axis_name in parts[:3]:
            if axis_name == "left":
                signs.append(1.0)
            elif axis_name == "right":
                signs.append(-1.0)
            elif axis_name == "posterior":
                signs.append(1.0)
            elif axis_name == "anterior":
                signs.append(-1.0)
            elif axis_name == "superior":
                signs.append(1.0)
            elif axis_name == "inferior":
                signs.append(-1.0)
            else:
                logger.warning(
                    "Unsupported NRRD space axis %r in %r; assuming LPS",
                    axis_name,
                    space,
                )
                return np.ones(3, dtype=float)
        return np.asarray(signs, dtype=float)

    @classmethod
    def _nrrd_z_geometry_lps(
        cls,
        header: dict[str, Any],
    ) -> tuple[np.ndarray, np.ndarray] | None:
        """Return (origin, z-direction) in DICOM LPS coordinates."""
        raw_origin = next(
            (
                header[key]
                for key in ("space origin", "space_origin", "spaceOrigin")
                if key in header and header[key] is not None
            ),
            None,
        )
        raw_directions = next(
            (
                header[key]
                for key in (
                    "space directions",
                    "space_directions",
                    "spaceDirections",
                )
                if key in header and header[key] is not None
            ),
            None,
        )
        if raw_origin is None or raw_directions is None:
            return None

        try:
            origin = np.asarray(raw_origin, dtype=float).reshape(-1)
        except (TypeError, ValueError):
            return None
        if origin.size != 3 or not np.all(np.isfinite(origin)):
            return None

        spatial_directions: list[np.ndarray] = []
        try:
            direction_entries = list(raw_directions)
        except TypeError:
            return None
        for entry in direction_entries:
            if entry is None:
                continue
            try:
                vector = np.asarray(entry, dtype=float).reshape(-1)
            except (TypeError, ValueError):
                continue
            if vector.size == 3 and np.all(np.isfinite(vector)):
                spatial_directions.append(vector)
        if len(spatial_directions) < 3:
            return None

        signs = cls._nrrd_space_to_lps_signs(header.get("space"))
        return origin * signs, spatial_directions[-1] * signs

    @classmethod
    def _align_labelmap_z_to_source_slices(
        cls,
        labelmap_np: np.ndarray,
        header: dict[str, Any],
        source_images: list[pydicom.Dataset],
    ) -> tuple[np.ndarray, bool, dict[str, Any]]:
        """Align mask frame 0 with source image frame 0 using patient geometry.

        ``labelmap_np`` is already in (Z, Y, X) order.  The NRRD Z direction
        determines whether increasing array indices move from the first sorted
        DICOM slice to the last one or in the opposite direction.  This avoids
        relying on InstanceNumber, file order, or a model-specific flip rule.
        """
        details: dict[str, Any] = {"mode": "unchanged"}
        if labelmap_np.ndim != 3 or labelmap_np.shape[0] <= 1:
            return labelmap_np, False, details

        nrrd_geometry = cls._nrrd_z_geometry_lps(header)
        if nrrd_geometry is None:
            details["mode"] = "metadata_unavailable"
            logger.warning(
                "Cannot determine NRRD Z direction; leaving mask frame order unchanged"
            )
            return labelmap_np, False, details

        source_positions = [cls._physical_slice_position(ds) for ds in source_images]
        if not source_positions or any(value is None for value in source_positions):
            details["mode"] = "dicom_positions_unavailable"
            logger.warning(
                "Cannot determine DICOM slice positions; leaving mask frame order unchanged"
            )
            return labelmap_np, False, details

        orientation = np.asarray(source_images[0].ImageOrientationPatient, dtype=float)
        normal = np.cross(orientation[:3], orientation[3:])
        normal_norm = float(np.linalg.norm(normal))
        if normal_norm == 0:
            details["mode"] = "invalid_dicom_orientation"
            return labelmap_np, False, details
        normal /= normal_norm

        origin_lps, z_direction_lps = nrrd_geometry
        mask_first = float(np.dot(origin_lps, normal))
        mask_last = float(
            np.dot(
                origin_lps + z_direction_lps * (labelmap_np.shape[0] - 1),
                normal,
            )
        )
        source_first = float(source_positions[0])
        source_last = float(source_positions[-1])

        direct_error = abs(mask_first - source_first) + abs(mask_last - source_last)
        reverse_error = abs(mask_first - source_last) + abs(mask_last - source_first)
        source_spacing = (
            float(np.median(np.abs(np.diff(np.asarray(source_positions, dtype=float)))))
            if len(source_positions) > 1
            else 0.0
        )
        tolerance = max(1e-3, source_spacing * 0.1)
        should_reverse = reverse_error + tolerance < direct_error

        details.update(
            {
                "mode": "physical_endpoint_match",
                "mask_first_position": mask_first,
                "mask_last_position": mask_last,
                "source_first_position": source_first,
                "source_last_position": source_last,
                "direct_error": direct_error,
                "reverse_error": reverse_error,
                "tolerance": tolerance,
            }
        )
        if should_reverse:
            logger.info(
                "Reversing MONAI mask Z frames to match physically sorted DICOM "
                "slices (direct_error=%.6f, reverse_error=%.6f)",
                direct_error,
                reverse_error,
            )
            details["mode"] = "physical_endpoint_match_reversed"
            return np.flip(labelmap_np, axis=0).copy(), True, details

        logger.info(
            "MONAI mask Z frames already match physically sorted DICOM slices "
            "(direct_error=%.6f, reverse_error=%.6f)",
            direct_error,
            reverse_error,
        )
        return labelmap_np, False, details

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
