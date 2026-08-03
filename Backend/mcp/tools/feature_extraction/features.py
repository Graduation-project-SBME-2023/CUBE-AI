"""
Radiomics & Feature Analysis MCP Tools — updated to embed chart_data in return payload.

CHANGES vs original:
  - radiomics_analyze_importance now returns chart_data containing:
      importance_bars   : [{feature, importance}]  (top top_n_plot)
      cumulative        : [{n_features, cumulative_importance}]
      correlation       : {labels:[str], matrix:[[float]]}  (top 20)
      pca               : [{x, y, target}]
      tsne              : [{x, y, target}]
      task_type         : str
      method            : str
    These arrays drive the React chart components directly — no CSV fetching needed.
"""

import json
import logging
import os
import re
import shutil
import tempfile
import time
from typing import Any, List, Optional, Union

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from .. import mcp
from ..helpers.target_columns import load_feature_schema, resolve_target_column
from ..helpers.workspace import WorkspacePath, base_on_workspace_dir
from .consolidation import (
    build_availability_audit,
    build_fusion_table,
    normalize_organ_name,
)

from tool_logging import ToolEvent, log_tool_state, TextPayload, ProgressPayload


# ── Helpers ────────────────────────────────────────────────────────────────────

def _run_extractor(image_path: str, mask_path: str, params_file: str) -> dict:
    from radiomics import featureextractor
    extractor = featureextractor.RadiomicsFeatureExtractor(params_file)
    result = extractor.execute(image_path, mask_path)
    return {str(k): v for k, v in result.items() if not k.startswith("diagnostics_")}

def _setup_logging(log_file: str, verbose: bool = True) -> None:
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO if verbose else logging.WARNING)
    for h in logger.handlers[:]:
        logger.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s - %(levelname)-8s - %(name)s - %(message)s")
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)

    logging.getLogger('radiomics').setLevel(logging.ERROR)


def _csv_str(value: Optional[Union[str, List[Any]]]) -> Optional[list]:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in value.split(",") if v.strip()]
    return None


def _csv_int(value: Optional[Union[int, str, List[Any]]]) -> Optional[list]:
    if value is None:
        return None
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        try:
            return [int(p) for p in value]
        except ValueError as exc:
            raise ValueError(f"Expected integer list, got: {value!r}") from exc
    parts = _csv_str(value)
    if parts is None:
        return None
    try:
        return [int(p) for p in parts]
    except ValueError as exc:
        raise ValueError(f"Expected comma-separated integers, got: {value!r}") from exc


def _csv_float(value: Optional[Union[float, int, str, List[Any]]]) -> Optional[list]:
    if value is None:
        return None
    if isinstance(value, (float, int)):
        return [float(value)]
    if isinstance(value, list):
        try:
            return [float(p) for p in value]
        except ValueError as exc:
            raise ValueError(f"Expected float list, got: {value!r}") from exc
    parts = _csv_str(value)
    if parts is None:
        return None
    try:
        return [float(p) for p in parts]
    except ValueError as exc:
        raise ValueError(f"Expected comma-separated floats, got: {value!r}") from exc


def _safe(v) -> Optional[float]:
    try:
        f = float(v)
        return None if (np.isnan(f) or np.isinf(f)) else round(f, 6)
    except Exception:
        return None


# ── Input Models ───────────────────────────────────────────────────────────────

class ExtractFeaturesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="ignore")

    image_types: Optional[Union[str, List[str]]] = Field(default=None)
    feature_classes: Optional[Union[str, List[str]]] = Field(default=None)
    specific_features: Optional[Union[str, List[str]]] = Field(default=None)
    mask_labels: Optional[Union[int, str, List[Union[int, str]]]] = Field(default=None)
    pixel_spacing: Optional[Union[float, str, List[Union[float, str]]]] = Field(default=None)
    image_pattern: str = Field(default="*.nii.gz")
    mask_pattern: str = Field(default="*.nii.gz")
    normalize: bool = Field(default=True)
    bin_width: float = Field(default=25.0)
    resample: bool = Field(default=True)
    force_2d: bool = Field(default=False)
    n_workers: int = Field(default=1, ge=1)
    targets_csv: WorkspacePath = Field(default=None)
    id_column: Optional[str] = Field(default=None)
    target_column: Optional[str] = Field(default=None)
    id_pattern: Optional[str] = Field(default=None)
    image_dir: WorkspacePath = Field(default=None)
    mask_dir: WorkspacePath = Field(default=None)
    output_dir: WorkspacePath = Field(default=None)
    organs: Optional[Union[str, List[str]]] = Field(
        default=None,
        description="Optional target/organ IDs to extract from the segmentation manifest",
    )
    require_all_organs: bool = Field(
        default=False,
        description="Fail the run when any cohort subject lacks a requested organ",
    )


class AnalyzeImportanceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="ignore")

    input_path: WorkspacePath = Field(...)
    target_column: Optional[str] = Field(
        default=None,
        description="Outcome column; omit to resolve it from feature_schema.json or the CSV",
    )
    top_features: Union[int, str, List[Union[int, str]]] = Field(default="10")
    task_type: Optional[str] = Field(default=None)
    method: str = Field(default="rf")
    encode_categorical: str = Field(default="auto")
    max_onehot_cardinality: int = Field(default=10)
    create_plots: bool = Field(default=True)
    top_n_plot: int = Field(default=30)


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="radiomics_extract_features",
    annotations={
        "title": "Extract Radiomic Features from Medical Images",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def radiomics_extract_features(params: ExtractFeaturesInput) -> str:
    """Extract radiomic features from medical images and segmentation masks using PyRadiomics.

    IMPORTANT — two separate concepts:
    - `feature_classes`: statistical/textural feature groups computed ON an image type.
      Valid values: 'firstorder', 'shape', 'glcm', 'glrlm', 'glszm', 'gldm', 'ngtdm', 'shape2D'.
      Do NOT put 'wavelet', 'LoG', 'Gradient', etc. here — those are image filter types.
    - `image_types`: image pre-processing filters applied before feature extraction.
      Valid values: 'Original', 'Wavelet', 'LoG', 'Gradient', 'Exponential', 'SquareRoot'.
      Default is 'Original,Wavelet' which covers the most common use case.
    """
    import glob as _glob
    import yaml

    try:
        import SimpleITK as sitk
        from radiomics import featureextractor
    except ImportError:
        return json.dumps({
            "status": "error",
            "error_message": "PyRadiomics / SimpleITK not installed. Run: pip install pyradiomics simpleitk",
        }, indent=2)

    output_dir = base_on_workspace_dir(params.output_dir) if params.output_dir else base_on_workspace_dir('features')

    # Resolve image_dir with auto-discovery fallback
    requested_image_dir = base_on_workspace_dir(params.image_dir) if params.image_dir else None
    if requested_image_dir and os.path.isdir(requested_image_dir):
        image_dir = requested_image_dir
    else:
        # Search workspace candidates if requested image_dir is missing or not provided
        candidates_img = ['volumes', 'images', 'series', 'data/volumes', 'data/images', 'data']
        found_img_dir = None
        for cand in candidates_img:
            cand_path = base_on_workspace_dir(cand)
            if cand_path and os.path.isdir(cand_path):
                files = _glob.glob(os.path.join(cand_path, params.image_pattern))
                if files:
                    found_img_dir = cand_path
                    break
                elif found_img_dir is None:
                    found_img_dir = cand_path
        image_dir = found_img_dir or (requested_image_dir or base_on_workspace_dir('volumes'))

    # Resolve mask_dir with auto-discovery fallback
    requested_mask_dir = base_on_workspace_dir(params.mask_dir) if params.mask_dir else None
    if requested_mask_dir and os.path.isdir(requested_mask_dir):
        mask_dir = requested_mask_dir
    else:
        candidates_mask = ['masks', 'segmentations', 'data/masks', 'data/segmentations']
        found_mask_dir = None
        for cand in candidates_mask:
            cand_path = base_on_workspace_dir(cand)
            if cand_path and os.path.isdir(cand_path):
                files = _glob.glob(os.path.join(cand_path, params.mask_pattern))
                if files:
                    found_mask_dir = cand_path
                    break
                elif found_mask_dir is None:
                    found_mask_dir = cand_path
        mask_dir = found_mask_dir or (requested_mask_dir or base_on_workspace_dir('masks'))

    os.makedirs(output_dir, exist_ok=True)
    log_file = os.path.join(output_dir, "radiomics_extraction.log")
    _setup_logging(log_file)
    logger = logging.getLogger(__name__)

    if requested_image_dir and requested_image_dir != image_dir:
        logger.warning(f"Requested image_dir '{requested_image_dir}' not found. Discovered image directory: '{image_dir}'")
    if requested_mask_dir and requested_mask_dir != mask_dir:
        logger.warning(f"Requested mask_dir '{requested_mask_dir}' not found. Discovered mask directory: '{mask_dir}'")

    image_types   = _csv_str(params.image_types)   or ["Original", "Wavelet"]
    feature_classes = _csv_str(params.feature_classes)
    specific_features = _csv_str(params.specific_features)
    mask_labels_raw = _csv_int(params.mask_labels)
    pixel_spacing  = _csv_float(params.pixel_spacing) or [1.0, 1.0, 1.0]
    requested_organs = (
        sorted({normalize_organ_name(value) for value in _csv_str(params.organs)})
        if params.organs
        else []
    )

    # ── Guard: catch image-type names mistakenly passed as feature_classes ──────
    # The LLM sometimes puts 'wavelet', 'LoG', 'Gradient' etc. in feature_classes.
    # These are image filter types, not feature classes. Silently rescue them.
    _valid_feature_classes = {"firstorder", "shape", "glcm", "glrlm", "glszm", "gldm", "ngtdm", "shape2d"}
    _valid_image_types = {"original", "wavelet", "log", "gradient", "exponential", "squareroot", "lbp2d", "lbp3d"}
    if feature_classes:
        rescued_image_types = [fc for fc in feature_classes if fc.lower() in _valid_image_types]
        feature_classes     = [fc for fc in feature_classes if fc.lower() in _valid_feature_classes]
        if rescued_image_types:
            logger.warning(
                f"[wavelet-guard] Moved {rescued_image_types} from feature_classes → image_types. "
                "These are image filter types, not feature classes."
            )
            # Capitalise to match PyRadiomics convention and merge with existing image_types
            image_types = list(set(image_types) | {t.capitalize() for t in rescued_image_types})
        if not feature_classes:
            feature_classes = None  # fall through to default below

    image_type_names = {
        "original": "Original", "wavelet": "Wavelet", "log": "LoG",
        "gradient": "Gradient", "exponential": "Exponential",
        "squareroot": "SquareRoot", "lbp2d": "LBP2D", "lbp3d": "LBP3D",
    }
    unknown_image_types = [value for value in image_types if value.lower() not in image_type_names]
    image_types = [image_type_names[value.lower()] for value in image_types if value.lower() in image_type_names]
    if unknown_image_types:
        logger.warning("Ignoring unsupported image type(s): %s", unknown_image_types)
    if not image_types:
        image_types = ["Original", "Wavelet"]

    feature_class_names = {
        "firstorder": "firstorder", "shape": "shape", "glcm": "glcm",
        "glrlm": "glrlm", "glszm": "glszm", "gldm": "gldm",
        "ngtdm": "ngtdm", "shape2d": "shape2D",
    }
    if feature_classes:
        feature_classes = [feature_class_names[value.lower()] for value in feature_classes]

    temp_dir = None
    try:
        for path, name in [(image_dir, "image_dir"), (mask_dir, "mask_dir")]:
            if not os.path.isdir(path):
                return json.dumps({
                    "status": "error",
                    "error_message": f"{name} does not exist or is not a directory: '{path}'. Please ensure images and masks exist before running feature extraction.",
                }, indent=2)

        if feature_classes is None and specific_features is None:
            feature_classes = ["firstorder", "shape", "glcm", "glrlm", "glszm", "gldm", "ngtdm"]

        pyrad_params: dict = {
            "imageType": {},
            "featureClass": {},
            "setting": {
                "force2D": params.force_2d,
                "normalize": params.normalize,
                "normalizeScale": 100,
                "binWidth": params.bin_width,
                "interpolator": "sitkBSpline",
                "correctMask": True,
                "padDistance": 10,
                "removeOutliers": 3,
            },
        }
        if params.resample:
            pyrad_params["setting"]["resampledPixelSpacing"] = pixel_spacing

        _img_type_map = {
            "Original": {}, "Wavelet": {}, "Exponential": {}, "Gradient": {},
            "SquareRoot": {}, "LBP2D": {}, "LBP3D": {},
            "LoG": {"sigma": [1.0, 2.0, 3.0, 4.0, 5.0]},
        }
        for it in image_types:
            pyrad_params["imageType"][it] = _img_type_map.get(it, {})

        if specific_features is None:
            for fc in feature_classes:
                pyrad_params["featureClass"][fc] = []
        else:
            for feat in specific_features:
                parts = feat.split("_")
                if len(parts) >= 2:
                    fc = parts[-2]
                    fn = parts[-1]
                    pyrad_params["featureClass"].setdefault(fc, [])
                    if fn not in pyrad_params["featureClass"][fc]:
                        pyrad_params["featureClass"][fc].append(fn)

        params_file = os.path.join(output_dir, "radiomics_params.yaml")
        with open(params_file, "w") as fh:
            yaml.dump(pyrad_params, fh)

        extractor = featureextractor.RadiomicsFeatureExtractor(params_file)

        target_dict: dict = {}
        targets_csv_path = params.targets_csv
        if not (targets_csv_path and os.path.exists(targets_csv_path)):
            targets_csv_path = base_on_workspace_dir('populations/targets/targets.csv')

        if not (targets_csv_path and os.path.exists(targets_csv_path)):
            return json.dumps({
                "status": "error",
                "error_message": (
                    f"targets.csv not found at '{targets_csv_path}'. "
                    "Feature extraction requires targets.csv to assign target labels for downstream importance analysis and modeling. "
                    "Run generate_targets (after extract_cohort_labels) before running feature extraction."
                ),
            }, indent=2)

        tdf = pd.read_csv(targets_csv_path)
        if tdf.empty:
            return json.dumps({
                "status": "error",
                "error_message": f"targets.csv at '{targets_csv_path}' is empty. Feature extraction requires valid target labels.",
            }, indent=2)

        id_col  = params.id_column or tdf.columns[0]
        try:
            tgt_col, target_resolution = resolve_target_column(
                tdf.columns,
                requested=params.target_column,
            )
        except ValueError as exc:
            return json.dumps({
                "status": "error",
                "error_message": str(exc),
            }, indent=2)
        if id_col not in tdf.columns or tgt_col not in tdf.columns:
            return json.dumps({
                "status": "error",
                "error_message": (
                    f"targets.csv must contain id column '{id_col}' and target column '{tgt_col}'. "
                    f"Available columns: {tdf.columns.tolist()}"
                ),
            }, indent=2)

        def _normalise_id(value: Any) -> str:
            value = str(value).strip().casefold()
            for extension in (".nii.gz", ".nii"):
                if value.endswith(extension):
                    return value[:-len(extension)]
            return value

        valid_targets = tdf.dropna(subset=[id_col, tgt_col]).copy()
        if valid_targets.empty:
            return json.dumps({
                "status": "error",
                "error_message": f"targets.csv at '{targets_csv_path}' has no rows with both an ID and target label.",
            }, indent=2)

        valid_targets[id_col] = valid_targets[id_col].astype(str).str.strip()
        duplicate_ids = valid_targets[id_col].duplicated(keep=False)
        if duplicate_ids.any():
            duplicates = sorted(valid_targets.loc[duplicate_ids, id_col].unique().tolist())
            return json.dumps({
                "status": "error",
                "error_message": f"targets.csv contains duplicate patient IDs: {duplicates[:10]}",
            }, indent=2)

        # Aliases map image/manifest identifiers back to the canonical cohort ID.
        target_dict: dict[str, Any] = {}
        canonical_subject_by_key: dict[str, str] = {}
        for _, row in valid_targets.iterrows():
            canonical_subject = str(row[id_col]).strip()
            key = _normalise_id(canonical_subject)
            target_dict[key] = row[tgt_col]
            canonical_subject_by_key[key] = canonical_subject
        # ALSO key by 'name' column (case_id / clinical_pid) if present,
        # so NIfTI filename stems (e.g. 'case_00005') match the correct label.
        if "name" in valid_targets.columns and "name" != id_col:
            for _, row in valid_targets.iterrows():
                if pd.isna(row["name"]):
                    continue
                name_val = _normalise_id(row["name"])
                if name_val and name_val not in target_dict:
                    target_dict[name_val] = row[tgt_col]
                    canonical_subject_by_key[name_val] = str(row[id_col]).strip()
        logger.info(
            "Loaded %d label keys from %s (id_col=%r, tgt_col=%r, resolution=%s)",
            len(target_dict), targets_csv_path, id_col, tgt_col, target_resolution,
        )

        image_files = sorted(_glob.glob(os.path.join(image_dir, params.image_pattern)))
        mask_files  = sorted(_glob.glob(os.path.join(mask_dir,  params.mask_pattern)))

        if not image_files:
            raise ValueError(f"No images found in {image_dir} matching '{params.image_pattern}'")
        if not mask_files:
            raise ValueError(f"No masks found in {mask_dir} matching '{params.mask_pattern}'")

        def _extract_id(filepath):
            filename = os.path.basename(filepath)
            if params.id_pattern:
                try:
                    m = re.search(params.id_pattern, filename)
                    if m and m.groups():
                        return m.group(1)
                except re.error:
                    pass
            for ext in (".nii.gz", ".nii"):
                if filename.endswith(ext):
                    return filename[: -len(ext)]
            return os.path.splitext(filename)[0]

        images_dict = {_extract_id(f): f for f in image_files}
        masks_dict  = {_extract_id(f): f for f in mask_files}

        manifest_path = base_on_workspace_dir("segmentation_manifest.json")
        pairs: dict[str, dict[str, str]] = {}
        if manifest_path and os.path.isfile(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as stream:
                segmentation_manifest = json.load(stream)
            for pair in segmentation_manifest.get("pairs", []):
                if pair.get("status") != "ready":
                    continue
                if pair.get("review_status") not in ("accepted", "modified"):
                    continue
                subject_id = str(pair.get("volume_uid") or "")
                target_id_raw = str(pair.get("target_id") or "")
                target_id = normalize_organ_name(target_id_raw) if target_id_raw else ""
                pair_id = str(pair.get("pair_id") or "")
                mask_rel = str(pair.get("mask_path") or "")
                image_path = images_dict.get(subject_id)
                mask_path = base_on_workspace_dir(mask_rel) if mask_rel else None
                if subject_id and target_id and pair_id and image_path and mask_path:
                    if os.path.isfile(mask_path):
                        pairs[pair_id] = {
                            "image": image_path,
                            "mask": mask_path,
                            "subject_id": subject_id,
                            "target_id": target_id,
                        }
            logger.info(
                "Loaded %d reviewed segmentation mask(s) from %s",
                len(pairs),
                manifest_path,
            )
        else:
            pairs = {
                sid: {
                    "image": images_dict[sid],
                    "mask": masks_dict[sid],
                    "subject_id": sid,
                    "target_id": "roi",
                }
                for sid in images_dict if sid in masks_dict
            }

        if requested_organs:
            pairs = {
                pair_id: files
                for pair_id, files in pairs.items()
                if files["target_id"] in requested_organs
            }

        if not pairs:
            raise ValueError("No matching image-mask pairs found.")

        paired_subject_count = len({files["subject_id"] for files in pairs.values()})
        unlabeled_subject_ids = sorted({
            files["subject_id"]
            for files in pairs.values()
            if _normalise_id(files["subject_id"]) not in target_dict
        })
        pairs = {
            pair_id: files for pair_id, files in pairs.items()
            if _normalise_id(files["subject_id"]) in target_dict
        }
        if not pairs:
            return json.dumps({
                "status": "error",
                "error_message": (
                    "No image-mask pair could be matched to a target label. "
                    "Check targets.csv identifiers or provide id_pattern/id_column."
                ),
                "unlabeled_subject_ids": unlabeled_subject_ids,
            }, indent=2)
        if unlabeled_subject_ids:
            logger.warning("Skipping %s image-mask pair(s) with no target label: %s", len(unlabeled_subject_ids), unlabeled_subject_ids)

        for files in pairs.values():
            files["cohort_subject_id"] = canonical_subject_by_key[
                _normalise_id(files["subject_id"])
            ]

        organs = requested_organs or sorted(
            {files["target_id"] for files in pairs.values()}
        )

        if mask_labels_raw is None:
            found: set = set()
            for mf in [files["mask"] for files in list(pairs.values())[:10]]:
                try:
                    arr = sitk.GetArrayFromImage(sitk.ReadImage(mf))
                    for lbl in np.unique(arr):
                        if lbl > 0:
                            found.add(int(lbl))
                except Exception:
                    pass
            mask_labels_raw = sorted(found) or [1]

        all_features_by_target: dict[str, list[dict[str, Any]]] = {}
        features_by_subject: dict[str, dict[str, Any]] = {}
        temp_dir = tempfile.mkdtemp(dir=output_dir)
        start = time.time()

        payload = TextPayload(message=f"Extracting features for {len(pairs)} masks...")
        log_tool_state(ToolEvent("tool_started", "text", payload))


        for idx, (pair_id, files) in enumerate(pairs.items(), 1):
            subject_id = files["subject_id"]
            cohort_subject_id = files["cohort_subject_id"]
            target_id = files["target_id"]
            logger.info(
                "Processing %s target=%s (%d/%d)",
                subject_id,
                target_id,
                idx,
                len(pairs),
            )
            payload = ProgressPayload(
                current=idx,
                total=len(pairs),
                label='Processing Volumes'
            )
            log_tool_state(ToolEvent("tool_progress", "progress", payload))
            try:
                orig_image = sitk.ReadImage(files["image"])
                orig_mask  = sitk.ReadImage(files["mask"])
                mask_array = sitk.GetArrayFromImage(orig_mask)

                for lbl in mask_labels_raw:
                    binary = (mask_array == lbl).astype(np.uint8)
                    if binary.sum() == 0:
                        continue
                    bin_img = sitk.GetImageFromArray(binary)
                    bin_img.CopyInformation(orig_mask)
                    tmp_mask_path = os.path.join(temp_dir, f"{subject_id}_lbl{lbl}.nii.gz")
                    sitk.WriteImage(bin_img, tmp_mask_path)
                    try:
                        import concurrent.futures
                        import multiprocessing
                        ctx = multiprocessing.get_context('spawn')
                        _n_workers = max(1, min(params.n_workers, os.cpu_count() or 1))
                        with concurrent.futures.ProcessPoolExecutor(max_workers=_n_workers, mp_context=ctx) as executor:
                            future = executor.submit(_run_extractor, files["image"], tmp_mask_path, params_file)
                            features = future.result(timeout=600)

                        target_value = target_dict[_normalise_id(subject_id)]
                        target_row = {
                            "subject_id": cohort_subject_id,
                            tgt_col: target_value,
                            **features,
                        }
                        all_features_by_target.setdefault(target_id, []).append(
                            target_row
                        )

                        wide_row = features_by_subject.setdefault(
                            cohort_subject_id,
                            {},
                        )
                        for feature_name, feature_value in features.items():
                            namespaced_feature = (
                                feature_name
                                if len(mask_labels_raw) == 1
                                else f"label_{lbl}__{feature_name}"
                            )
                            wide_row[f"{target_id}__{namespaced_feature}"] = feature_value
                    except concurrent.futures.TimeoutError:
                        logger.error(f"  {subject_id} label {lbl}: extraction timed out after 10 minutes — skipping")
                    except concurrent.futures.process.BrokenProcessPool:
                        logger.error(f"  {subject_id} label {lbl}: extraction crashed (OOM or Segfault) — skipping volume")
                    except Exception as exc:
                        logger.error(f"  {subject_id} label {lbl}: extraction failed — {exc}")
                    finally:
                        if os.path.exists(tmp_mask_path):
                            os.remove(tmp_mask_path)
            except Exception as exc:
                logger.error(f"Error processing {subject_id}: {exc}")

        processing_time = time.time() - start
        payload = TextPayload(message='Feature Extraction...Done')
        log_tool_state(ToolEvent('tool_finished', 'text', payload))

        csv_paths: dict = {}
        num_features: dict = {}

        for target_id, rows in all_features_by_target.items():
            if not rows:
                continue
            df = pd.DataFrame(rows)
            id_cols = [c for c in df.columns if c.lower() in ("id", "subject_id", "patientid")]
            tgt_cols = [tgt_col] if tgt_col in df.columns else []
            feat_cols = sorted([c for c in df.columns if c not in id_cols + tgt_cols])
            df = df[id_cols + tgt_cols + feat_cols]
            csv_path = os.path.join(
                output_dir, f"radiomic_features_target_{target_id}.csv"
            )
            df.to_csv(csv_path, index=False)
            csv_paths[f"target_{target_id}"] = csv_path
            num_features[target_id] = len(feat_cols)

        default_csv_path = os.path.join(output_dir, "radiomics_features.csv")
        cohort_table = valid_targets[[id_col, tgt_col]].copy()
        default_feature_table = build_fusion_table(
            cohort_table,
            features_by_subject,
            organs,
            id_col,
            tgt_col,
        )
        default_feature_table.to_csv(default_csv_path, index=False)
        csv_paths["radiomics_features"] = default_csv_path

        organ_tables_dir = os.path.join(output_dir, "organ_feature_tables")
        os.makedirs(organ_tables_dir, exist_ok=True)
        feature_columns_by_organ: dict[str, list[str]] = {}
        for organ in organs:
            organ_columns = sorted(
                column
                for column in default_feature_table.columns
                if column.startswith(f"{organ}__")
            )
            feature_columns_by_organ[organ] = organ_columns
            organ_table_path = os.path.join(organ_tables_dir, f"{organ}.csv")
            default_feature_table[
                ["subject_id", tgt_col, f"{organ}_present", *organ_columns]
            ].to_csv(organ_table_path, index=False)
            csv_paths[f"organ_{organ}"] = organ_table_path

        audit = build_availability_audit(default_feature_table, organs)
        available_pairs = {
            (files["cohort_subject_id"], files["target_id"])
            for files in pairs.values()
        }
        failed_mask = (
            (audit["present"] == 0)
            & audit.apply(
                lambda row: (row["subject_id"], row["organ"]) in available_pairs,
                axis=1,
            )
        )
        audit.loc[failed_mask, "status"] = "extraction_failed_or_empty"
        audit.loc[(audit["present"] == 0) & ~failed_mask, "status"] = "segmentation_missing"
        audit_path = os.path.join(output_dir, "organ_availability_audit.csv")
        audit.to_csv(audit_path, index=False)
        csv_paths["organ_availability_audit"] = audit_path

        schema = {
            "subject_id_column": "subject_id",
            "target_column": tgt_col,
            "source_target_column": tgt_col,
            "target_resolution": target_resolution,
            "targets_csv": str(targets_csv_path),
            "organ_prefix_delimiter": "__",
            "organs": organs,
            "presence_columns": [f"{organ}_present" for organ in organs],
            "feature_columns_by_organ": feature_columns_by_organ,
        }
        schema_path = os.path.join(output_dir, "feature_schema.json")
        with open(schema_path, "w", encoding="utf-8") as stream:
            json.dump(schema, stream, indent=2)
        csv_paths["feature_schema"] = schema_path

        missing_required = audit[audit["present"] == 0]
        if params.require_all_organs and not missing_required.empty:
            return json.dumps({
                "status": "error",
                "error_message": (
                    f"{len(missing_required)} required patient-organ combinations are missing"
                ),
                "csv_paths": csv_paths,
                "missing_patient_organs": missing_required.to_dict(orient="records"),
            }, indent=2)

        if not features_by_subject:
            return json.dumps({
                "status": "error",
                "error_message": "No radiomic features were extracted from any matched image-mask pair.",
                "csv_paths": csv_paths,
                "paired_subjects": paired_subject_count,
                "labeled_subjects": len(pairs),
            }, indent=2)

        return json.dumps({
            "status": "success",
            "csv_paths": csv_paths,
            "num_subjects": len(default_feature_table),
            "num_subjects_with_features": len(features_by_subject),
            "num_volume_target_pairs": len(pairs),
            "paired_subjects": paired_subject_count,
            "unlabeled_subject_ids": unlabeled_subject_ids,
            "num_features": num_features,
            "organs": organs,
            "target_column": tgt_col,
            "missing_patient_organs": int((audit["present"] == 0).sum()),
            "mask_labels": mask_labels_raw,
            "params_file": params_file,
            "processing_time_seconds": round(processing_time, 2),
        }, indent=2)

    except Exception as exc:
        logger.error(f"Unhandled error: {exc}", exc_info=True)
        return json.dumps({"status": "error", "error_message": str(exc)}, indent=2)

    finally:
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass


@mcp.tool(
    name="radiomics_analyze_importance",
    annotations={
        "title": "Analyse Feature Importance and Select Top Features",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def radiomics_analyze_importance(params: AnalyzeImportanceInput) -> str:
    """Perform feature importance analysis. Returns chart_data for frontend rendering.

    Returns:
        str: JSON with keys:
            - status, output_dir, task_type, method, feature_count, result_files
            - chart_data: {
                importance_bars : [{feature, importance}]  — top top_n_plot features
                cumulative      : [{n_features, cumulative_pct}]  — full curve
                correlation     : {labels:[str], matrix:[[float|null]]}  — top 20
                pca             : [{x, y, target}]  — 2D PCA of top-N subset
                tsne            : [{x, y, target}] | null
                task_type       : str
                method          : str
              }
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns
    from sklearn.decomposition import PCA
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.feature_selection import (
        RFE, SelectKBest, f_classif, f_regression,
        mutual_info_classif, mutual_info_regression,
    )
    from sklearn.manifold import TSNE
    from sklearn.preprocessing import LabelEncoder

    output_dir = base_on_workspace_dir('features')
    os.makedirs(output_dir, exist_ok=True)
    log_file = os.path.join(output_dir, "feature_importance_analysis.log")
    _setup_logging(log_file)
    logger = logging.getLogger(__name__)

    # Count the main pipeline stages for progress reporting:
    #   1. Data loading & preprocessing
    #   2. Importance computation
    #   3. Importance bars + cumulative chart data
    #   4. Correlation chart data
    #   5. PCA chart data
    #   6. t-SNE chart data
    #   7. Archival plots
    #   8. Top-N CSV export
    TOTAL_STAGES = 8

    try:
        if not os.path.exists(params.input_path):
            raise ValueError(f"Input file not found: {params.input_path}")
        valid_methods = ("rf", "f_test", "mutual_info", "rfe")
        if params.method not in valid_methods:
            logger.warning(f"Unsupported method '{params.method}'. Falling back to 'rf'.")
            params.method = "rf"
        valid_enc = ("auto", "onehot", "label", "target", "none")
        if params.encode_categorical not in valid_enc:
            logger.warning(f"Unsupported encode_categorical '{params.encode_categorical}'. Falling back to 'auto'.")
            params.encode_categorical = "auto"

        top_features_list = sorted({n for n in (_csv_int(params.top_features) or [10]) if n > 0})
        if not top_features_list:
            raise ValueError("top_features must contain at least one positive integer.")

        # ── Stage 1: Data loading & preprocessing ──────────────────────────
        payload = TextPayload(message=f"Analysing feature importance for {os.path.basename(params.input_path)}...")
        log_tool_state(ToolEvent("tool_started", "text", payload))

        df = pd.read_csv(params.input_path)
        target_column, target_resolution = resolve_target_column(
            df.columns,
            requested=params.target_column,
            schema=load_feature_schema(params.input_path),
        )

        X = df.drop(columns=[target_column])
        id_cols_to_drop = [
            c for c in X.columns
            if c.lower() in ("id", "subject_id", "subjectid", "patientid", "patient_id", "case_id", "caseid", "name", "uid", "pat_id", "unnamed: 0")
            or c.lower().endswith("_id")
        ]
        if id_cols_to_drop:
            X = X.drop(columns=id_cols_to_drop)
        y = df[target_column]

        if X.shape[1] == 0:
            raise ValueError("No usable feature columns remain after removing identifiers and the target column.")
        if y.isna().any():
            raise ValueError("The target column contains missing values.")

        if params.task_type:
            task_type = params.task_type
        elif y.dtype == "object" or y.dtype.name == "category":
            task_type = "classification"
        elif len(y.unique()) / len(y) < 0.05 or len(y.unique()) < 10:
            task_type = "classification"
        else:
            task_type = "regression"
        logger.info(f"Task type: {task_type}")

        X_original = X.copy()
        cat_cols = X.select_dtypes(include=["object", "category"]).columns.tolist()
        encoding_mappings: dict = {}

        if params.encode_categorical != "none" and cat_cols:
            for col in cat_cols:
                n_unique = X[col].nunique()
                method = params.encode_categorical
                if method == "auto":
                    method = "onehot" if n_unique <= params.max_onehot_cardinality else "label"
                if method == "onehot":
                    dummies = pd.get_dummies(X[col], prefix=col, drop_first=True)
                    X = pd.concat([X, dummies], axis=1)
                    encoding_mappings[col] = {"method": "onehot", "categories": X_original[col].unique().tolist()}
                elif method == "label":
                    le = LabelEncoder()
                    X[f"{col}_label"] = le.fit_transform(X[col].astype(str))
                    encoding_mappings[col] = {"method": "label", "mapping": dict(zip(le.classes_, range(len(le.classes_))))}
                elif method == "target":
                    tmp = pd.concat([X[[col]], y], axis=1)
                    means = tmp.groupby(col)[target_column].mean()
                    X[f"{col}_target"] = X[col].map(means)
                    X[f"{col}_freq"]   = X[col].map(X[col].value_counts(normalize=True))
                    encoding_mappings[col] = {"method": "target"}
            X = X.drop(columns=cat_cols)
        elif params.encode_categorical == "none" and cat_cols:
            X = X.select_dtypes(include=[np.number])

        num_cols = X.select_dtypes(include=[np.number]).columns
        X[num_cols] = X[num_cols].fillna(X[num_cols].median())
        X = X.fillna(0)

        if encoding_mappings:
            enc_path = os.path.join(output_dir, "categorical_encodings.json")
            with open(enc_path, "w") as fh:
                json.dump(encoding_mappings, fh, indent=2, default=str)

        logger.info(f"Feature matrix: {X.shape[0]} rows × {X.shape[1]} features")

        payload = ProgressPayload(current=1, total=TOTAL_STAGES, label="Loading & preprocessing data")
        log_tool_state(ToolEvent("tool_progress", "progress", payload))

        # ── Stage 2: Importance computation ────────────────────────────────
        def _rf_importance(n):
            clf = (RandomForestClassifier if task_type == "classification"
                   else RandomForestRegressor)(n_estimators=100, random_state=42)
            clf.fit(X, y)
            imp = clf.feature_importances_
            idx = np.argsort(imp)[::-1][:n]
            return X.columns[idx].tolist(), imp

        def _f_test_importance(n):
            fn = f_classif if task_type == "classification" else f_regression
            sel = SelectKBest(fn, k=min(n, X.shape[1])).fit(X, y)
            return X.columns[sel.get_support()].tolist(), sel.scores_

        def _mutual_info_importance(n):
            fn = mutual_info_classif if task_type == "classification" else mutual_info_regression
            sel = SelectKBest(fn, k=min(n, X.shape[1])).fit(X, y)
            return X.columns[sel.get_support()].tolist(), sel.scores_

        def _rfe_importance(n):
            est = (RandomForestClassifier if task_type == "classification"
                   else RandomForestRegressor)(n_estimators=100, random_state=42)
            sel = RFE(est, n_features_to_select=min(n, X.shape[1]), step=0.1).fit(X, y)
            # Lower RFE rankings are better; negate them so the common
            # descending sort below retains the selected features first.
            return X.columns[sel.get_support()].tolist(), -sel.ranking_

        method_fn = {"rf": _rf_importance, "f_test": _f_test_importance,
                     "mutual_info": _mutual_info_importance, "rfe": _rfe_importance}[params.method]

        all_feats, scores = method_fn(X.shape[1])
        importance_df = pd.DataFrame({"feature": X.columns, "importance": scores})
        importance_df = importance_df.sort_values("importance", ascending=False).reset_index(drop=True)

        importance_path = os.path.join(output_dir, "feature_importance.csv")
        importance_df.to_csv(importance_path, index=False)

        payload = ProgressPayload(current=2, total=TOTAL_STAGES, label=f"Computing importance ({params.method}, {task_type})")
        log_tool_state(ToolEvent("tool_progress", "progress", payload))

        # ── Build chart_data ────────────────────────────────────────────────
        chart_data: dict = {"task_type": task_type, "method": params.method}

        # ── Stage 3: Importance bars + cumulative curve ─────────────────────
        top_plot = importance_df.head(min(params.top_n_plot, len(importance_df)))
        chart_data["importance_bars"] = [
            {"feature": str(row["feature"]), "importance": _safe(row["importance"])}
            for _, row in top_plot.iterrows()
        ]

        total_imp = importance_df["importance"].sum()
        cum = importance_df["importance"].cumsum()
        cum_pct = (cum / total_imp * 100) if total_imp > 0 else cum * 0
        step = max(1, len(cum_pct) // 300)
        chart_data["cumulative"] = [
            {"n_features": int(i + 1), "cumulative_pct": round(float(v), 3)}
            for i, v in enumerate(cum_pct)
            if i % step == 0 or i == len(cum_pct) - 1
        ]

        payload = ProgressPayload(current=3, total=TOTAL_STAGES, label="Building importance & cumulative chart data")
        log_tool_state(ToolEvent("tool_progress", "progress", payload))

        # ── Stage 4: Correlation matrix (top 20) ───────────────────────────
        top20_feats = importance_df["feature"].iloc[:20].tolist()
        top20_feats = [f for f in top20_feats if f in X.columns]
        if len(top20_feats) >= 2:
            corr = X[top20_feats].corr()
            chart_data["correlation"] = {
                "labels": top20_feats,
                "matrix": [
                    [_safe(corr.iloc[r, c]) for c in range(len(top20_feats))]
                    for r in range(len(top20_feats))
                ],
            }
        else:
            chart_data["correlation"] = None

        payload = ProgressPayload(current=4, total=TOTAL_STAGES, label="Computing correlation matrix (top 20 features)")
        log_tool_state(ToolEvent("tool_progress", "progress", payload))

        # ── Stage 5: PCA ───────────────────────────────────────────────────
        top_n_scatter = min(top_features_list[-1], X.shape[1])
        scatter_feats = [f for f in importance_df["feature"].iloc[:top_n_scatter].tolist() if f in X.columns]
        chart_data["pca"] = None
        chart_data["tsne"] = None

        if len(scatter_feats) >= 2:
            X_sub = X[scatter_feats]

            try:
                pca = PCA(n_components=2, random_state=42)
                Xp = pca.fit_transform(X_sub)
                pca_df = pd.DataFrame({"x": Xp[:, 0], "y": Xp[:, 1], "target": y.values})
                chart_data["pca"] = {
                    "points": [
                        {"x": round(float(r["x"]), 4), "y": round(float(r["y"]), 4),
                         "target": str(r["target"])}
                        for _, r in pca_df.iterrows()
                    ],
                    "explained_variance": [round(float(v), 4) for v in pca.explained_variance_ratio_],
                    "n_features": len(scatter_feats),
                }
            except Exception as exc:
                logger.warning(f"PCA failed: {exc}")

        payload = ProgressPayload(current=5, total=TOTAL_STAGES, label="Running PCA")
        log_tool_state(ToolEvent("tool_progress", "progress", payload))

        # ── Stage 6: t-SNE ─────────────────────────────────────────────────
        if len(scatter_feats) >= 2 and len(X_sub) <= 5000:
            try:
                perp = min(30, len(X_sub) - 1)
                tsne = TSNE(n_components=2, perplexity=perp, random_state=42, n_jobs=-1)
                Xt = tsne.fit_transform(X_sub)
                tsne_df = pd.DataFrame({"x": Xt[:, 0], "y": Xt[:, 1], "target": y.values})
                chart_data["tsne"] = {
                    "points": [
                        {"x": round(float(r["x"]), 4), "y": round(float(r["y"]), 4),
                         "target": str(r["target"])}
                        for _, r in tsne_df.iterrows()
                    ],
                    "n_features": len(scatter_feats),
                }
            except Exception as exc:
                logger.warning(f"t-SNE failed: {exc}")

        tsne_label = "Running t-SNE" if (len(scatter_feats) >= 2 and len(X_sub) <= 5000) else "Skipping t-SNE (dataset > 5 000 samples)"
        payload = ProgressPayload(current=6, total=TOTAL_STAGES, label=tsne_label)
        log_tool_state(ToolEvent("tool_progress", "progress", payload))

        # ── Stage 7: Archival plots ─────────────────────────────────────────
        plots_dir = os.path.join(output_dir, "plots")
        os.makedirs(plots_dir, exist_ok=True)

        if params.create_plots:
            try:
                plt.figure(figsize=(12, 8))
                plot_df = importance_df.head(min(params.top_n_plot, len(importance_df)))
                sns.barplot(x="importance", y="feature", data=plot_df)
                plt.title(f"Top {len(plot_df)} Feature Importance ({params.method})")
                plt.tight_layout()
                plt.savefig(os.path.join(plots_dir, "feature_importance_plot.png"), dpi=300, bbox_inches="tight")
                plt.close()
            except Exception as exc:
                logger.warning(f"Bar chart failed: {exc}"); plt.close()

            try:
                plt.figure(figsize=(10, 6))
                cum_series = importance_df["importance"].cumsum() / importance_df["importance"].sum()
                plt.plot(range(1, len(cum_series) + 1), cum_series)
                for thr, color in zip([0.8, 0.9, 0.95, 0.99], ["r", "g", "orange", "purple"]):
                    n = int((cum_series >= thr).argmax()) + 1
                    plt.axhline(thr, color=color, linestyle="--", label=f"{thr*100:.0f}%: {n} features")
                plt.xlabel("Number of Features"); plt.ylabel("Cumulative Importance")
                plt.title("Cumulative Feature Importance"); plt.legend(loc="lower right")
                plt.tight_layout()
                plt.savefig(os.path.join(plots_dir, "cumulative_importance_plot.png"), dpi=300, bbox_inches="tight")
                plt.close()
            except Exception as exc:
                logger.warning(f"Cumulative plot failed: {exc}"); plt.close()

            if len(top20_feats) >= 2:
                try:
                    corr = X[top20_feats].corr()
                    plt.figure(figsize=(12, 10))
                    sns.heatmap(corr, mask=np.triu(np.ones_like(corr, dtype=bool)),
                                cmap=sns.diverging_palette(230, 20, as_cmap=True),
                                vmax=1, vmin=-1, center=0, square=True,
                                annot=True, fmt=".2f", linewidths=0.5)
                    plt.title("Correlation Matrix – Top 20 Features"); plt.tight_layout()
                    plt.savefig(os.path.join(plots_dir, "feature_correlation_heatmap.png"), dpi=300, bbox_inches="tight")
                    plt.close()
                except Exception as exc:
                    logger.warning(f"Correlation heatmap failed: {exc}"); plt.close()

        payload = ProgressPayload(current=7, total=TOTAL_STAGES, label="Saving archival plot images")
        log_tool_state(ToolEvent("tool_progress", "progress", payload))

        # ── Stage 8: Export top-N feature CSVs ─────────────────────────────
        selected_files: dict = {}
        for n in top_features_list:
            n = min(n, X.shape[1])
            if params.method == "rfe":
                sel_feats, _ = _rfe_importance(n)
            else:
                sel_feats = importance_df["feature"].iloc[:n].tolist()

            cols_to_save = [c for c in sel_feats if c in df.columns]
            encoded_only = [c for c in sel_feats if c not in df.columns]
            out_df = df[cols_to_save + [target_column]].copy() if cols_to_save else pd.DataFrame({target_column: df[target_column]})
            for col in encoded_only:
                if col in X.columns:
                    out_df[col] = X[col].values

            out_path = os.path.join(output_dir, f"top_{n}_features.csv")
            out_df.to_csv(out_path, index=False)
            selected_files[str(n)] = out_path

        payload = ProgressPayload(current=8, total=TOTAL_STAGES, label="Exporting top-N feature CSVs")
        log_tool_state(ToolEvent("tool_progress", "progress", payload))

        logger.info("Feature importance analysis complete.")
        payload = TextPayload(message="Feature Importance Analysis...Done")
        log_tool_state(ToolEvent("tool_finished", "text", payload))

        return json.dumps({
            "status": "success",
            "output_dir": output_dir,
            "task_type": task_type,
            "target_column": target_column,
            "target_resolution": target_resolution,
            "method": params.method,
            "feature_count": int(X.shape[1]),
            "result_files": {
                "importance": importance_path,
                "selected_features": selected_files,
            },
            # ── NEW: chart data for frontend rendering ──────────────────────
            "chart_data": chart_data,
        }, indent=2)

    except Exception as exc:
        logging.error(f"Unhandled error in radiomics_analyze_importance: {exc}", exc_info=True)
        return json.dumps({
            "status": "error",
            "error_message": str(exc),
            "input_path": params.input_path,
            "output_dir": output_dir,
        }, indent=2)


if __name__ == "__main__":
    mcp.run()
