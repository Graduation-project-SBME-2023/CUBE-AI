import httpx
import os
import logging
import json
import asyncio
from datetime import datetime
from functools import partial
from itertools import permutations
from typing import Annotated, Any, Dict, NotRequired, TypedDict
from pydantic import Field
import highdicom as hd
from pydicom.sr.codedict import codes
import numpy as np
import io
import pydicom
from pydicom.uid import generate_uid
import SimpleITK as sitk

from services.timeout_policy import (
    CONTROL_HTTP_TIMEOUT,
    SEGMENTATION_JOB_TIMEOUT_SECONDS,
    SEGMENTATION_POLL_SECONDS,
    SEGMENTATION_RESULT_READ_SECONDS,
    SEGMENTATION_UNAVAILABLE_GRACE_SECONDS,
)

from .utils import (
    SEGMENTATION_GEOMETRY_VERSION,
    parse_multipart_response,
    parse_nrrd_data,
    ensure_required_patient_tags
)

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
    x_axis_flipped: NotRequired[bool]
    y_axis_flipped: NotRequired[bool]
    axis_permutation: NotRequired[dict[str, str]]
    slice_alignment: NotRequired[dict[str, Any]]
    geometry_version: NotRequired[str]


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
            "geometry_version": SEGMENTATION_GEOMETRY_VERSION,
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

        # Keep the pre-alignment shape for diagnostics.  The actual correction
        # below is physical-space resampling, not index-only resize/flip logic.
        original_shape = tuple(int(value) for value in labelmap_np.shape)
        labelmap_np, axis_flips, axis_alignment = await asyncio.to_thread(
            self._align_labelmap_axes_to_source_slices,
            labelmap_np,
            header,
            dicom_datasets,
            allow_source_index_fallback=clean_model.lower() == "voxtell",
        )
        result["z_axis_reversed"] = axis_flips["z"]
        result["x_axis_flipped"] = axis_flips["x"]
        result["y_axis_flipped"] = axis_flips["y"]
        result["axis_permutation"] = axis_alignment.get("axis_mapping", {})
        result["slice_alignment"] = axis_alignment

        # The returned array is already on the exact DICOM grid.  Do NOT use
        # ndimage.zoom here: shape-only resizing ignores origin, spacing and
        # direction and can create a geometrically wrong DICOM-SEG.
        labelmap_np = (labelmap_np > 0).astype(np.uint8)
        expected_shape = (num_slices, int(rows), int(cols))
        if tuple(labelmap_np.shape) != expected_shape:
            raise ValueError(
                "Physical mask resampling did not produce the source DICOM shape: "
                f"got {tuple(labelmap_np.shape)}, expected {expected_shape}"
            )

        result["geometry_adjusted"] = bool(
            axis_alignment.get("geometry_adjusted", False)
        )
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
                # Target identity lives in SegmentLabel/target_label. Keep the
                # human-readable SeriesDescription free of encoded model/time
                # fields so no runtime code is tempted to parse provenance.
                series_description=f"Segmentation: {target_label}",
                instance_number=1,
                manufacturer='Manufacturer Name',
                manufacturer_model_name='MONAI Label Backend',
                software_versions=SEGMENTATION_GEOMETRY_VERSION,
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
    def _nrrd_axis_geometry_lps(
        cls,
        header: dict[str, Any],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
        """Return (origin, x-direction, y-direction, z-direction) in DICOM LPS.

        The three direction vectors correspond, in order, to the NRRD's own
        axis 0 (i, fastest-varying), axis 1 (j), and axis 2 (k, slices) -
        the same order as ``header["sizes"]``.
        """
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
        return (
            origin * signs,
            spatial_directions[0] * signs,
            spatial_directions[1] * signs,
            spatial_directions[2] * signs,
        )

    @staticmethod
    def _unit_vector(vector: np.ndarray, *, name: str) -> np.ndarray:
        length = float(np.linalg.norm(vector))
        if not np.isfinite(length) or length <= 0:
            raise ValueError(f"{name} has zero/invalid length")
        return np.asarray(vector, dtype=float) / length

    @classmethod
    def _describe_axis_mapping(
        cls,
        nrrd_directions_lps: tuple[np.ndarray, np.ndarray, np.ndarray],
        dicom_axes_lps: tuple[np.ndarray, np.ndarray, np.ndarray],
    ) -> tuple[dict[str, bool], dict[str, Any]]:
        """Describe NRRD-to-DICOM axis permutation/sign for diagnostics only.

        The mapping is intentionally NOT applied as a manual transpose/flip.
        Physical-space resampling below is authoritative and handles flips,
        permutations and oblique orientations in one operation without
        double-transforming the mask.
        """
        nrrd_names = ("nrrd_x", "nrrd_y", "nrrd_z")
        dicom_names = ("x", "y", "z")
        nrrd_unit = [
            cls._unit_vector(vector, name=name)
            for vector, name in zip(nrrd_directions_lps, nrrd_names)
        ]
        dicom_unit = [
            cls._unit_vector(vector, name=f"dicom_{name}")
            for vector, name in zip(dicom_axes_lps, dicom_names)
        ]
        similarity = np.asarray(
            [
                [float(np.dot(src, dst)) for dst in dicom_unit]
                for src in nrrd_unit
            ],
            dtype=float,
        )

        # permutation[target_axis] = source_nrrd_axis.  Evaluate all six
        # one-to-one assignments and choose the strongest absolute direction
        # match, so X/Y swaps are detected instead of being mistaken for
        # "no flip".
        best_permutation = max(
            permutations(range(3)),
            key=lambda permutation: sum(
                abs(similarity[permutation[target_axis], target_axis])
                for target_axis in range(3)
            ),
        )

        flips: dict[str, bool] = {}
        mapping: dict[str, str] = {}
        confidence: dict[str, float] = {}
        signed_similarity: dict[str, float] = {}
        for target_axis, target_name in enumerate(dicom_names):
            source_axis = best_permutation[target_axis]
            score = float(similarity[source_axis, target_axis])
            mapping[target_name] = nrrd_names[source_axis]
            flips[target_name] = score < 0
            confidence[target_name] = abs(score)
            signed_similarity[target_name] = score

        details = {
            "axis_mapping": mapping,
            "axis_match_confidence": confidence,
            "axis_signed_similarity": signed_similarity,
            "direction_similarity_matrix": similarity.tolist(),
        }
        if min(confidence.values()) < 0.95:
            details["axis_mapping_warning"] = (
                "NRRD and DICOM axes are not nearly parallel; physical "
                "resampling is required and manual flips would be unsafe"
            )
        return flips, details

    @staticmethod
    def _dicom_reference_geometry(
        source_images: list[pydicom.Dataset],
    ) -> dict[str, Any]:
        if not source_images:
            raise ValueError("Cannot build reference geometry from an empty DICOM series")

        first = source_images[0]
        orientation = np.asarray(first.ImageOrientationPatient, dtype=float)
        position = np.asarray(first.ImagePositionPatient, dtype=float)
        if orientation.size != 6 or position.size != 3:
            raise ValueError(
                "DICOM ImageOrientationPatient/ImagePositionPatient are required "
                "for safe mask alignment"
            )

        row_cosine = orientation[:3]
        col_cosine = orientation[3:]
        row_cosine = row_cosine / np.linalg.norm(row_cosine)
        col_cosine = col_cosine / np.linalg.norm(col_cosine)
        normal = np.cross(row_cosine, col_cosine)
        normal = normal / np.linalg.norm(normal)

        pixel_spacing = np.asarray(first.PixelSpacing, dtype=float).reshape(-1)
        if pixel_spacing.size != 2 or np.any(pixel_spacing <= 0):
            raise ValueError("DICOM PixelSpacing must contain two positive values")

        positions = [
            SegmentationService._physical_slice_position(ds)
            for ds in source_images
        ]
        if not positions or any(value is None for value in positions):
            raise ValueError(
                "DICOM physical slice positions are required for safe mask alignment"
            )
        positions_np = np.asarray(positions, dtype=float)
        if len(positions_np) > 1:
            deltas = np.diff(positions_np)
            if np.any(deltas <= 0):
                raise ValueError(
                    "Source DICOM slices must be physically sorted with unique positions"
                )
            slice_spacing = float(np.median(deltas))
        else:
            slice_spacing = float(
                getattr(first, "SpacingBetweenSlices", 0)
                or getattr(first, "SliceThickness", 1.0)
                or 1.0
            )
        if slice_spacing <= 0:
            raise ValueError("DICOM slice spacing must be positive")

        # SimpleITK index X increases across DICOM columns (row cosine),
        # index Y across DICOM rows (column cosine), and index Z across the
        # physically sorted slice normal.  DICOM PixelSpacing is [row, col].
        direction_matrix = np.column_stack(
            (row_cosine, col_cosine, normal)
        )
        return {
            "size_xyz": (int(first.Columns), int(first.Rows), len(source_images)),
            "spacing_xyz": (
                float(pixel_spacing[1]),
                float(pixel_spacing[0]),
                slice_spacing,
            ),
            "origin_lps": tuple(float(value) for value in position),
            "direction_matrix": direction_matrix,
            "axes_lps": (row_cosine, col_cosine, normal),
            "slice_positions": positions_np,
        }

    @staticmethod
    def _physical_geometry_matches(
        *,
        source_size_xyz: tuple[int, int, int],
        source_spacing_xyz: tuple[float, float, float],
        source_origin_lps: tuple[float, float, float],
        source_direction: np.ndarray,
        target: dict[str, Any],
        tolerance: float = 1e-4,
    ) -> bool:
        return (
            tuple(source_size_xyz) == tuple(target["size_xyz"])
            and np.allclose(
                source_spacing_xyz,
                target["spacing_xyz"],
                rtol=tolerance,
                atol=tolerance,
            )
            and np.allclose(
                source_origin_lps,
                target["origin_lps"],
                rtol=tolerance,
                atol=tolerance,
            )
            and np.allclose(
                source_direction,
                target["direction_matrix"],
                rtol=tolerance,
                atol=tolerance,
            )
        )

    @classmethod
    def _align_labelmap_axes_to_source_slices(
        cls,
        labelmap_np: np.ndarray,
        header: dict[str, Any],
        source_images: list[pydicom.Dataset],
        *,
        allow_source_index_fallback: bool = False,
    ) -> tuple[np.ndarray, dict[str, bool], dict[str, Any]]:
        """Resample the NRRD mask onto the exact DICOM patient-space grid.

        ``labelmap_np`` is in (Z, Y, X) order.  We reconstruct its physical
        geometry from the NRRD origin/direction vectors, reconstruct the target
        grid from DICOM IOP/IPP/PixelSpacing, and use nearest-neighbor
        SimpleITK resampling.  This handles axis flips, X/Y permutations,
        oblique orientation and spacing/origin differences without relying on
        array shape or manual flip heuristics.
        """
        if labelmap_np.ndim != 3:
            raise ValueError(
                f"Expected a 3-D MONAI mask after transpose, got {labelmap_np.shape}"
            )

        target = cls._dicom_reference_geometry(source_images)
        expected_shape = (
            int(target["size_xyz"][2]),
            int(target["size_xyz"][1]),
            int(target["size_xyz"][0]),
        )
        nrrd_geometry = cls._nrrd_axis_geometry_lps(header)
        if nrrd_geometry is None:
            raw_sizes = header.get("sizes")
            try:
                nrrd_sizes = tuple(
                    int(value) for value in np.asarray(raw_sizes).reshape(-1)
                )
            except (TypeError, ValueError):
                nrrd_sizes = ()
            expected_nrrd_sizes = (
                int(target["size_xyz"][0]),
                int(target["size_xyz"][1]),
                int(target["size_xyz"][2]),
            )
            size_contract_matches = (
                tuple(labelmap_np.shape) == expected_shape
                and nrrd_sizes
                in {
                    expected_nrrd_sizes,
                    (1, *expected_nrrd_sizes),
                }
            )
            if allow_source_index_fallback and size_contract_matches:
                # VoxTell explicitly restores its prediction to the input
                # image orientation before writing.  Older responses were
                # accidentally written as (1, X, Y, Z) with a 4-D identity
                # header, so their physical metadata is unusable even though
                # their voxel indices exactly match the source volume.  This
                # narrowly checked compatibility path salvages those cached
                # results without permitting shape-only resampling.
                aligned = (labelmap_np > 0).astype(np.uint8, copy=True)
                foreground = int(np.count_nonzero(aligned))
                flips = {"x": False, "y": False, "z": False}
                details: dict[str, Any] = {
                    "mode": "source_index_grid_fallback",
                    "geometry_adjusted": True,
                    "index_order_preserved": True,
                    "geometry_source": "source_dicom_index_contract",
                    "fallback_reason": "nrrd_physical_geometry_unavailable",
                    "axis_mapping": {"x": "nrrd_x", "y": "nrrd_y", "z": "nrrd_z"},
                    "axis_match_confidence": {"x": 1.0, "y": 1.0, "z": 1.0},
                    "source": {
                        "nrrd_sizes": list(nrrd_sizes),
                        "array_shape_zyx": list(labelmap_np.shape),
                    },
                    "target": {
                        "size_xyz": list(target["size_xyz"]),
                        "spacing_xyz": list(target["spacing_xyz"]),
                        "origin_lps": list(target["origin_lps"]),
                        "direction": target["direction_matrix"].tolist(),
                    },
                    "foreground_before": foreground,
                    "foreground_after": foreground,
                    "foreground_retention_ratio": 1.0,
                }
                logger.warning(
                    "Using validated VoxTell source-index geometry fallback for "
                    "legacy/cached NRRD sizes=%s and DICOM shape=%s",
                    nrrd_sizes,
                    expected_shape,
                )
                return aligned, flips, details
            raise ValueError(
                "NRRD physical geometry is missing or invalid; refusing to create "
                "a potentially misaligned DICOM-SEG"
            )
        origin_lps, x_dir_lps, y_dir_lps, z_dir_lps = nrrd_geometry

        nrrd_directions = (x_dir_lps, y_dir_lps, z_dir_lps)
        source_spacing = tuple(
            float(np.linalg.norm(direction)) for direction in nrrd_directions
        )
        if any(not np.isfinite(value) or value <= 0 for value in source_spacing):
            raise ValueError(
                f"NRRD space directions contain invalid spacing: {source_spacing}"
            )

        source_direction_matrix = np.column_stack(
            [
                cls._unit_vector(direction, name=f"nrrd_axis_{index}")
                for index, direction in enumerate(nrrd_directions)
            ]
        )
        if abs(float(np.linalg.det(source_direction_matrix))) < 1e-6:
            raise ValueError("NRRD direction matrix is singular")

        flips, mapping_details = cls._describe_axis_mapping(
            nrrd_directions,
            target["axes_lps"],
        )

        source_size_xyz = (
            int(labelmap_np.shape[2]),
            int(labelmap_np.shape[1]),
            int(labelmap_np.shape[0]),
        )
        source_origin = tuple(float(value) for value in origin_lps)
        geometry_adjusted = not cls._physical_geometry_matches(
            source_size_xyz=source_size_xyz,
            source_spacing_xyz=source_spacing,
            source_origin_lps=source_origin,
            source_direction=source_direction_matrix,
            target=target,
        )

        details: dict[str, Any] = {
            "mode": "physical_resample" if geometry_adjusted else "unchanged",
            "geometry_adjusted": geometry_adjusted,
            "flips": dict(flips),
            **mapping_details,
            "source": {
                "size_xyz": list(source_size_xyz),
                "spacing_xyz": list(source_spacing),
                "origin_lps": list(source_origin),
                "direction": source_direction_matrix.tolist(),
            },
            "target": {
                "size_xyz": list(target["size_xyz"]),
                "spacing_xyz": list(target["spacing_xyz"]),
                "origin_lps": list(target["origin_lps"]),
                "direction": target["direction_matrix"].tolist(),
            },
        }

        # Preserve the useful first/last-slice diagnostics for the original Z
        # bug, even though the actual correction is now full 3-D resampling.
        normal = target["axes_lps"][2]
        mask_first = float(np.dot(origin_lps, normal))
        mask_last = float(
            np.dot(
                origin_lps + z_dir_lps * (labelmap_np.shape[0] - 1),
                normal,
            )
        )
        source_positions = target["slice_positions"]
        source_first = float(source_positions[0])
        source_last = float(source_positions[-1])
        direct_error = abs(mask_first - source_first) + abs(mask_last - source_last)
        reverse_error = abs(mask_first - source_last) + abs(mask_last - source_first)
        details.update(
            {
                "mask_first_position": mask_first,
                "mask_last_position": mask_last,
                "source_first_position": source_first,
                "source_last_position": source_last,
                "direct_error": direct_error,
                "reverse_error": reverse_error,
            }
        )

        foreground_before = int(np.count_nonzero(labelmap_np))
        source_mask = sitk.GetImageFromArray(
            (labelmap_np > 0).astype(np.uint8)
        )
        source_mask.SetOrigin(source_origin)
        source_mask.SetSpacing(source_spacing)
        source_mask.SetDirection(
            tuple(float(value) for value in source_direction_matrix.reshape(-1))
        )

        reference = sitk.Image(target["size_xyz"], sitk.sitkUInt8)
        reference.SetOrigin(target["origin_lps"])
        reference.SetSpacing(target["spacing_xyz"])
        reference.SetDirection(
            tuple(float(value) for value in target["direction_matrix"].reshape(-1))
        )

        if geometry_adjusted:
            resampler = sitk.ResampleImageFilter()
            resampler.SetReferenceImage(reference)
            resampler.SetInterpolator(sitk.sitkNearestNeighbor)
            resampler.SetTransform(sitk.Transform(3, sitk.sitkIdentity))
            resampler.SetDefaultPixelValue(0)
            aligned_image = resampler.Execute(source_mask)
            aligned = sitk.GetArrayFromImage(aligned_image).astype(np.uint8)
        else:
            aligned = (labelmap_np > 0).astype(np.uint8, copy=True)

        if tuple(aligned.shape) != expected_shape:
            raise ValueError(
                "Physical resampling returned an unexpected shape: "
                f"{tuple(aligned.shape)} vs {expected_shape}"
            )

        foreground_after = int(np.count_nonzero(aligned))
        details["foreground_before"] = foreground_before
        details["foreground_after"] = foreground_after
        details["foreground_retention_ratio"] = (
            float(foreground_after) / float(foreground_before)
            if foreground_before
            else 1.0
        )
        if foreground_before > 0 and foreground_after == 0:
            raise ValueError(
                "Mask had foreground before physical resampling but none after it; "
                "NRRD and DICOM geometry are inconsistent"
            )
        if foreground_before > 0 and foreground_after < foreground_before * 0.5:
            logger.warning(
                "Physical mask alignment retained only %.1f%% of foreground voxels",
                100.0 * foreground_after / foreground_before,
            )

        logger.info(
            "Aligned MONAI mask to DICOM grid: mode=%s mapping=%s flips=%s "
            "shape=%s -> %s",
            details["mode"],
            details["axis_mapping"],
            flips,
            source_size_xyz[::-1],
            aligned.shape,
        )
        return aligned, flips, details

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
