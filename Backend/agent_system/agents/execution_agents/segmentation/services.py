import httpx
import os
import logging
import json
from datetime import datetime
import logging
from typing import Annotated
from pydantic import Field
from typing import List, Dict, Any, Union
from requests_toolbelt.multipart.decoder import MultipartDecoder 
import highdicom as hd
from pydicom.sr.codedict import codes
import numpy as np
from pathlib import Path
import io
import pydicom
import tempfile

logger = logging.getLogger(__name__)

from .utils import (
    parse_multipart_response,
    parse_nrrd_data,
    ensure_required_patient_tags
)

logger = logging.getLogger(__name__)


class SegmentationService:

    def __init__(self):

        self.monai_url = os.environ.get("MONAI_URL", "http://localhost:8000")
        self.orthanc_url = os.environ.get("ORTHANC_URL", "http://localhost:8043")


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
        
        timeout_config = httpx.Timeout(
            connect=30.0,
            read=1800.0,
            write=30.0,
            pool=30.0
        ) 
        FrameOfReferenceUID = None 
        study_instance_uid = None
        # =========== get the label info & organs ================
        
        if isinstance(label_info, list):
            organ_list = [str(o).strip().lower() for o in label_info if str(o).strip()]

        elif isinstance(label_info, str):
            organ_list = [o.strip().lower() for o in label_info.split(",") if o.strip()]
        else:
            organ_list = ["segmentation"]
        
        # =========== get the study from the series UID ===========
        get_study_url = f"{self.monai_url}/volumes/{series_instance_uid}/study"
        async with httpx.AsyncClient(timeout=timeout_config) as client:
            try:
                study_response = await client.get(get_study_url)
                if study_response.status_code == 200:
                    study_info = study_response.json()
                    study_instance_uid = study_info.get('study_uid')
                    logger.info(f"Retrieved StudyInstanceUID: {study_instance_uid} for SeriesInstanceUID: {series_instance_uid}")
                else:
                    logger.warning(f"Failed to retrieve study info for SeriesInstanceUID: {series_instance_uid}, status code: {study_response.status_code}")
            except Exception as e:
                logger.warning(f"Error retrieving study info for SeriesInstanceUID: {series_instance_uid}: {e}")
        
        # ====the inference using monai label=====

        infer_url = f"{self.monai_url}/infer/{clean_model}"
        query_params = {"image": series_instance_uid, "output": "all"}
        inference_params = params or {}
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
                logger.info(f"Injected Class={class_index} for model='{clean_model}', label='{organ_list}'")
            else:
                logger.warning(f"No Class index found for model='{clean_model}', labels='{organ_list}' — model may fail")

        files = {"params": (None, json.dumps(inference_params), "application/json")}      
        async with httpx.AsyncClient(timeout=timeout_config) as client:  
            try:
                inference_start = datetime.now()
                response = await client.post(infer_url, params=query_params, files=files)
                inference_duration = (datetime.now() - inference_start).total_seconds()
                
                logger.info(f"Inference completed in {inference_duration:.1f}s with status: {response.status_code}")
                
            except httpx.TimeoutException as e:
                elapsed = (datetime.now() - start_time).total_seconds()
                logger.error(f"Inference timeout after {elapsed:.1f}s: {str(e)}")
                return {
                    "error": True, 
                    "stage": "inference_timeout", 
                    "message": f"Inference exceeded timeout after {elapsed:.1f}s. Try with smaller volume or different model.",
                    "elapsed_seconds": elapsed
                }
            except Exception as e:
                logger.error(f"Inference request failed: {str(e)}")
                return {"error": True, "stage": "inference_request", "message": str(e)}

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
            logger.error(f"Inference failed with status {response.status_code}: {response.text[:200]}")
            return result          
            
        # ===== parsing the response into nrrd and centroids ============
        nrrd_data, centroids = parse_multipart_response(
            response.headers,
            response.content
        )
        logger.info(f"Parsed multipart response: nrrd_data_size={len(nrrd_data)} bytes, centroids={list(centroids)}")
        
        # ==== parse the nrrd and extrace centroids ijk =====
        
        # parse the nrrd data into numpy array and header
        image, header = parse_nrrd_data(nrrd_data)
        # check if the problem in monai infer it self "post transform"
        print("=" * 50)
        print("MONAI OUTPUT")
        print("Shape:", image.shape)
        print("Unique:", np.unique(image))
        print("NRRD header sizes:", header.get("sizes"))
        print("NRRD space directions:", header.get("space directions"))
        print("NRRD space origin:", header.get("space origin"))
        print("=" * 50)

        logger.info(f"Parsed NRRD data: image_shape={image.shape}, image_dtype={image.dtype}, header_keys={list(header.keys())}")
        async with httpx.AsyncClient(timeout=timeout_config) as client:
            try:
                response_info = await client.get(f"{self.monai_url}/info/")
        
            except Exception as e:
                logger.warning(f"Error fetching MONAI Label info: {e}")
                
        labels = response_info.json().get("labels", [])
        logger.info(f"Available labels in MONAI Label: {labels}")
        
        # the centroid world and image coordinates 
        
        centroids_ijk = {}

        for key, value in centroids.items():
            segment_index = int(value[0])           # same as parseInt(..., 10)
            image_coords = [float(v) for v in value[1:]]   # slice(1:) → [1:]
            centroids_ijk[segment_index] = {
                "image": image_coords,
                "world": []                         # empty list, same as in JS
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
            "FrameOfReferenceUID": FrameOfReferenceUID,
            "centroidsIJK": centroids_ijk
        }
        
        logger.info(f"Constructed segmentation object with {len(segmentation_object['segments'])} segments")
        
        # ========== create pydicom datasets for the original series ============
        dicom_datasets = await self.fetch_source_datasets_from_orthanc(
            series_instance_uid=series_instance_uid
        )
        print("=" * 50)
        print("SOURCE CT")
        print("Rows:", dicom_datasets[0].Rows)
        print("Cols:", dicom_datasets[0].Columns)
        print("Slices:", len(dicom_datasets))
        print("=" * 50)
        num_slices = len(dicom_datasets)
        rows = dicom_datasets[0].Rows
        cols = dicom_datasets[0].Columns

        
        print("Rows, Columns, slices:", rows, cols, num_slices)


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
        # MONAI returns (1, X, Y, Z) — squeeze the leading batch/channel dim to get (X, Y, Z)
        if labelmap_np.ndim == 4 and labelmap_np.shape[0] == 1:
            labelmap_np = labelmap_np.squeeze(0)
        labelmap_np = np.transpose(labelmap_np, (2, 1, 0))  # (X, Y, Z) → (Z, Y, X) = (slices, rows, cols)
        print(f"Labelmap shape after squeeze+transpose: {labelmap_np.shape}")

        # VISTA returns class-indexed values (e.g. liver = class 5, so pixel value = 5).
        # highdicom BINARY segmentation only understands pixel values 0 (background) or 1 (segment 1).
        # Any other value causes "Pixel array contains segments that lack descriptions".
        # Binarise: any non-zero voxel belongs to our target segment → value = 1.
        unique_vals = np.unique(labelmap_np)
        print(f"Unique pixel values before binarisation: {unique_vals}")
        labelmap_np = (labelmap_np > 0).astype(np.uint8)
        print(f"After binarisation — unique values: {np.unique(labelmap_np)}, "
              f"foreground voxels: {int(labelmap_np.sum())}")
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
        # =========== create the DICOM-SEG  ================
        print("TYPE:", type(dicom_datasets[0]))
        print("IS DATASET:", isinstance(dicom_datasets[0], pydicom.dataset.Dataset))
        now = datetime.now()
        series_time = now.strftime("%I:%M:%S %p")

        #check before going to highdicom
        print("=" * 50)
        print("SEG INPUT")
        print("CT slices:", len(dicom_datasets))
        print("Mask shape:", labelmap_np.shape)
        print("=" * 50)

        seg = hd.seg.Segmentation(
                source_images=dicom_datasets,  # references + geometry
                pixel_array=labelmap_np,        # 3D labelmap → auto 2D frames
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
        # check if the problem in high dicom
        print("=" * 50)
        print("HIGHDICOM")
        print("NumberOfFrames:", seg.NumberOfFrames)

        if hasattr(seg, "PerFrameFunctionalGroupsSequence"):
            print(
                "Referenced frames:",
                len(seg.PerFrameFunctionalGroupsSequence)
            )

        print("=" * 50)

        logger.info(f"Created DICOM-SEG object with SeriesInstanceUID: {seg.SeriesInstanceUID}")    
        
        # ========== saving to orthanc ====================
        
        seg_bytes_io = io.BytesIO()
        seg.save_as(seg_bytes_io)
        seg_bytes_io.seek(0)
        async with httpx.AsyncClient(timeout=timeout_config) as client:
            headers = {"Content-Type": "application/dicom"}
            response = await client.post(f"{self.orthanc_url}/instances", headers=headers, content=seg_bytes_io.read())

            if response.status_code in (200, 201):
                logger.info(f"DICOM-SEG successfully uploaded to Orthanc. InstanceID: {response.json()['ID']}")
            else:
                logger.error(f"Failed to upload DICOM-SEG: {response.status_code} {response.text}")

        
        
        return result


    async def _resolve_class_index(self, model_name: str, organ_list: list) -> int | None:
        """
        Query /models/ and look up the Class integer index for the requested organ(s).
        Required by VISTA and similar models that need `params={"Class": <int>}`.
        Returns the first matching index, or None if the model has no Class mapping.
        """
        timeout_config = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                response = await client.get(f"{self.monai_url}/models/")
                if response.status_code != 200:
                    return None
                models = response.json()
        except Exception as e:
            logger.warning(f"_resolve_class_index: failed to fetch models: {e}")
            return None

        for model in models:
            if model.get("name", "").lower() != model_name.lower():
                continue
            params_schema = model.get("params_schema") or {}
            class_mapping = (params_schema.get("Class") or {}).get("mapping") or {}
            if not class_mapping:
                return None  # model has no Class mapping — skip
            for organ in organ_list:
                # Try exact match first, then normalised (underscores ↔ spaces)
                if organ in class_mapping:
                    return class_mapping[organ]
                normalised = organ.replace(" ", "_")
                if normalised in class_mapping:
                    return class_mapping[normalised]
            logger.warning(f"_resolve_class_index: organ(s) {organ_list} not in Class mapping for '{model_name}'. "
                           f"Available: {list(class_mapping.keys())[:10]}...")
            return None

        logger.warning(f"_resolve_class_index: model '{model_name}' not found in /models/")
        return None

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
            logger.info(f"Searching series by SeriesInstanceUID: {series_instance_uid}")

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
            logger.info(f"Resolved Orthanc series_id: {series_id}")

            # get  list of Instance IDs
            series_response = await client.get(f"{self.orthanc_url}/series/{series_id}")
            series_response.raise_for_status()
            series_data = series_response.json()
            instance_ids = series_data.get("Instances", [])

            if not instance_ids:
                raise ValueError(f"No instances found in series {series_instance_uid}")

            logger.info(f"Found {len(instance_ids)} instances in series {series_instance_uid}")

            # download each instance file and parse to pydicom.Dataset
            temp_dir = Path(tempfile.mkdtemp(prefix="orthanc-dicom-"))
            dicom_paths = [] 

            try:
                for i, inst_id in enumerate(instance_ids):
                    file_url = f"{self.orthanc_url}/instances/{inst_id}/file"
                    file_response = await client.get(file_url)
                    file_response.raise_for_status()

                    # save to tmp
                    temp_path = temp_dir / f"instance_{i:06d}.dcm"
                    with open(temp_path, "wb") as f:
                        f.write(file_response.content)

                    # Load with pydicom to get InstanceNumber for sorting
                    try:
                        ds = pydicom.dcmread(str(temp_path))
                        dicom_paths.append((ds, temp_path))
                        logger.debug(f"Saved & read {inst_id} → {temp_path}")
                    except Exception as e:
                        logger.warning(f"Failed pydicom read for {inst_id}: {e}")
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
                logger.info(f"Loaded and sorted {len(source_images)} pydicom datasets")
                return source_images

            finally:
                # Cleanup temp files
                for p in temp_dir.glob("*.dcm"):
                    p.unlink(missing_ok=True)
                temp_dir.rmdir()
