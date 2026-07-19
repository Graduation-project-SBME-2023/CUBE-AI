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
from .medical_vocabulary import (
    ANATOMICAL_SYNONYMS,
    PATHOLOGY_SYNONYMS,
    STRUCTURAL_ALIASES,
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
        inference_params["result_dtype"] = "uint16"
        inference_params["result_compress"] = False
        inference_params["restore_label_idxs"] = True

        # ---- Inject model-specific class index (required by VISTA) ----
        # VISTA and similar models expose a `Class` mapping in their params_schema.
        # Without it, the inference crashes with KeyError: 'Class'.
        if "Class" not in inference_params:
            class_index = await self._resolve_class_index(clean_model, organ_list)
            if class_index is not None:
                inference_params["Class"] = class_index
                logger.info(
                    "Injected Class=%s for model='%s', labels=%s",
                    class_index,
                    clean_model,
                    organ_list,
                )
            else:
                if clean_model.lower() == "vista":
                    msg = f"VISTA requires a Class index but none was found for labels: {organ_list}"
                    logger.error(msg)
                    return {"error": True, "stage": "upload", "message": msg}
                
                logger.warning(
                    "No Class index found for model='%s', labels=%s; model may fail",
                    clean_model,
                    organ_list,
                )

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
        
        # ==== parse the nrrd and extrace centroids ijk =====
        
        # parse the nrrd data into numpy array and header
        image, header = parse_nrrd_data(nrrd_data)
        logger.info(
            "MONAI output: shape=%s unique=%s nrrd_sizes=%s space_directions=%s space_origin=%s",
            image.shape,
            np.unique(image),
            header.get("sizes"),
            header.get("space directions"),
            header.get("space origin"),
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
                name=f'Automatic Segmentation',
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

        # VISTA returns class-indexed values (e.g. liver = class 5, gallbladder = 4, ...).
        # highdicom BINARY segmentation only understands pixel values 0 or 1.
        # We MUST extract only the target organ's class index voxels before binarising.
        # Using `> 0` would merge ALL segmented organs (gallbladder, spleen, liver, etc.)
        # into a single mask, causing the wrong organ to appear highlighted in the GIF.
        unique_vals = np.unique(labelmap_np)
        logger.info("Unique pixel values before binarisation: %s", unique_vals)

        target_class_index = inference_params.get("Class")  # the index we sent to VISTA
        if target_class_index is not None and len(unique_vals) > 2:
            # Multi-class output: isolate only the requested organ's class
            logger.info(
                "Multi-class VISTA output detected. Extracting organ class index=%s for label=%s",
                target_class_index,
                organ_list,
            )
            labelmap_np = (labelmap_np == int(target_class_index)).astype(np.uint8)
        else:
            # Binary output ({0, 1} or {0, 255}) or no class info — safe to use > 0
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
                logger.info(f"DICOM-SEG successfully uploaded to Orthanc. InstanceID: {response.json()['ID']}")
            else:
                logger.error("Failed to upload DICOM-SEG: %s %s", response.status_code, response.text)
                result["upload_error"] = True
                result["upload_status_code"] = response.status_code
                result["upload_response"] = response.text[:500]

        
        
        return result

    @staticmethod
    def _normalize_label_info(label_info) -> list[str]:
        if isinstance(label_info, list):
            return [str(label).strip().lower() for label in label_info if str(label).strip()]
        if isinstance(label_info, str):
            return [label.strip().lower() for label in label_info.split(",") if label.strip()]
        return ["segmentation"]


    async def _resolve_class_index(self, model_name: str, organ_list: list) -> int | None:
        """
        Query /models/ and look up the Class integer index for the requested organ(s).
        Required by VISTA and similar models that need `params={"Class": <int>}`.
        Returns the first matching index, or None if the model has no Class mapping.
        """
        try:
            async with httpx.AsyncClient(timeout=QUICK_TIMEOUT) as client:
                response = await client.get(f"{self.monai_url}/models/")
                if response.status_code != 200:
                    return None
                models = response.json()
        except Exception:
            logger.exception("_resolve_class_index: failed to fetch models")
            return None

        for model in models:
            if model.get("name", "").lower() != model_name.lower():
                continue
            params_schema = model.get("params_schema") or {}
            class_mapping = (params_schema.get("Class") or {}).get("mapping") or {}
            if not class_mapping:
                return None
            for organ in organ_list:
                for candidate in self._class_key_candidates(organ):
                    if candidate in class_mapping:
                        return class_mapping[candidate]
            logger.warning(
                "_resolve_class_index: organs %s not in Class mapping for '%s'. Available: %s...",
                organ_list,
                model_name,
                list(class_mapping.keys())[:10],
            )
            return None

        logger.warning("_resolve_class_index: model '%s' not found in /models/", model_name)
        return None

    @staticmethod
    def _class_key_candidates(label: str) -> list[str]:
        """
        Generate all fuzzy variant forms of a label for robust catalog lookup.
        Synonym/alias tables are imported from medical_vocabulary.py — edit there.

        Handles:
        - Word order:   "left kidney" ↔ "kidney_left"
        - Plurals:      "tumors" → "tumor", "lesions" → "lesion"
        - Separators:   underscores / spaces / hyphens are interchangeable
        - Anatomical synonyms: hepatic=liver, renal=kidney, splenic=spleen, etc.
        - Pathology synonyms: cancer=carcinoma=tumor; lesion=mass=nodule
        - Structural aliases: large_intestine=colon, portal_vein=portal_vein_and_splenic_vein, etc.
        """
        raw = str(label or "").lower().strip()
        # Normalise separators: hyphens and spaces → underscore
        normalized = "_".join(raw.replace("-", " ").split())
        candidates = [normalized]

        # ── 1. Laterality word-order swap ────────────────────────────────────
        parts = normalized.split("_")
        if parts and parts[0] in {"left", "right"} and len(parts) > 1:
            candidates.append("_".join(parts[1:] + [parts[0]]))
        elif parts and parts[-1] in {"left", "right"} and len(parts) > 1:
            candidates.append("_".join([parts[-1]] + parts[:-1]))

        # ── 2. Plural → singular stripping ───────────────────────────────────
        plural_stripped = normalized.rstrip("s") if normalized.endswith("s") else None
        if plural_stripped and plural_stripped != normalized:
            candidates.append(plural_stripped)
            ps_parts = plural_stripped.split("_")
            if ps_parts and ps_parts[0] in {"left", "right"} and len(ps_parts) > 1:
                candidates.append("_".join(ps_parts[1:] + [ps_parts[0]]))
            elif ps_parts and ps_parts[-1] in {"left", "right"} and len(ps_parts) > 1:
                candidates.append("_".join([ps_parts[-1]] + ps_parts[:-1]))

        # ── 3. Anatomical synonyms (from medical_vocabulary.py) ───────────────
        for syn, replacements in ANATOMICAL_SYNONYMS.items():
            if syn in normalized:
                for rep in replacements:
                    candidates.append(normalized.replace(syn, rep))

        # ── 4. Pathology synonyms (from medical_vocabulary.py) ───────────────
        for syn, replacements in PATHOLOGY_SYNONYMS.items():
            if syn in normalized:
                for rep in replacements:
                    candidates.append(normalized.replace(syn, rep))

        # ── 5. Structural aliases (from medical_vocabulary.py) ───────────────
        alias_val = STRUCTURAL_ALIASES.get(normalized)
        if alias_val:
            if isinstance(alias_val, list):
                candidates.extend(alias_val)
            else:
                candidates.append(alias_val)

        return list(dict.fromkeys(candidates))

    async def fetch_source_datasets_from_orthanc(
        self,
        series_instance_uid,
        timeout: float = 300.0
    )-> list[pydicom.Dataset]:
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

            # get  list of Instance IDs
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
