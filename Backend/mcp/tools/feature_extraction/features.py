"""Radiomics & Feature Analysis MCP Tools — updated to embed chart_data in return payload."""

import asyncio
import concurrent.futures
import io
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
from ..helpers import domain_api
from ..helpers.workspace import WorkspacePath, base_on_workspace_dir
from storage_paths import CACHE_DIR, TEMP_DIR

from tool_logging import ToolEvent, log_tool_state, TextPayload, ProgressPayload

FEATURE_EXTRACTION_TIMEOUT_SECONDS = float(
    os.environ.get("FEATURE_EXTRACTION_TIMEOUT_SECONDS", "600")
)

# ADDED FOR UI ENHANCEMENT — Import helper modules for Tasks 2, 3, 4.
# Wrapped in try/except so a missing file (e.g. first-run before deploy) never
# crashes the import of the whole features module.
try:
    from .cache_utils import get_cache_key, load_from_cache, save_to_cache
    from .validation_utils import check_mask_voxels, check_spatial_alignment
    _HELPERS_AVAILABLE = True
except ImportError:
    _HELPERS_AVAILABLE = False

from . import digests as provenance
from .feature_set_builder import (
    FEATURE_SET_SCHEMA_VERSION,
    SUBJECT_KEY,
    FeatureSetBuildError,
    finite_or_none,
    frame_from_csv,
    matrix_from_frame,
    quality_summary as build_quality_summary,
    selection_document,
    wide_table_from_rows,
)
from .selection_split import plan_selection_split


# ── Helpers ────────────────────────────────────────────────────────────────────

def _run_extractor(image_path: str, mask_path: str, params_file: str) -> dict:
    from radiomics import featureextractor
    extractor = featureextractor.RadiomicsFeatureExtractor(params_file)
    result = extractor.execute(image_path, mask_path)
    return {str(k): v for k, v in result.items() if not k.startswith("diagnostics_")}


def _terminate_process_pool(executor: concurrent.futures.ProcessPoolExecutor) -> None:
    processes = getattr(executor, "_processes", None) or {}
    for process in list(processes.values()):
        if process.is_alive():
            process.terminate()
    executor.shutdown(wait=False, cancel_futures=True)

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


# ADDED FOR UI ENHANCEMENT — Task 8: Recursive JSON sanitizer.
# Standard json.dumps() raises ValueError on np.nan / np.inf and TypeError on
# numpy scalar types (np.float32, np.int64).  React's JSON.parse also rejects
# JavaScript NaN literals.  Apply _json_serializable() to any nested structure
# before serialising so the frontend never receives invalid JSON.
def _json_serializable(obj):
    """Recursively convert numpy scalars and NaN/inf/±∞ to JSON-safe Python types."""
    if isinstance(obj, dict):
        return {k: _json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_serializable(v) for v in obj]
    if isinstance(obj, float):
        if obj != obj or obj == float("inf") or obj == float("-inf"):
            return None
        return obj
    if isinstance(obj, bool):
        return obj
    # Handle numpy scalar types (np.float32, np.float64, np.int32, np.int64, etc.)
    try:
        if hasattr(obj, "item"):
            val = obj.item()  # converts numpy scalar → native Python
            if isinstance(val, float) and (
                val != val or val in (float("inf"), float("-inf"))
            ):
                return None
            return val
    except Exception:
        pass
    return obj

def _label_free_qc(X: pd.DataFrame, names: List[str], stability_scores: list) -> dict:
    """`feature_sets.analysis` — label-free QC only."""
    columns = [name for name in names if name in X.columns]
    qc: dict = {"schema_version": FEATURE_SET_SCHEMA_VERSION, "feature_count": len(names)}
    top = columns[:20]
    if len(top) >= 2:
        corr = X[top].corr()
        qc["correlation"] = {
            "labels": top,
            "matrix": [[_safe(corr.iloc[r, c]) for c in range(len(top))] for r in range(len(top))],
        }
    wanted = set(columns)
    qc["stability"] = [item for item in stability_scores if item.get("feature") in wanted][:50]
    return _json_serializable(qc)


def build_fusion_table(cohort_table, features_by_subject, organs, id_col, tgt_col):
    cohort_df = cohort_table.copy()
    cohort_df.rename(columns={id_col: "subject_id"}, inplace=True)
    if not features_by_subject:
        for organ in organs:
            cohort_df[f"{organ}_present"] = 0
        return cohort_df
    features_df = pd.DataFrame.from_dict(features_by_subject, orient="index")
    if "target" in features_df.columns:
        features_df = features_df.drop(columns=["target"])
    cohort_df["subject_id"] = cohort_df["subject_id"].astype(str)
    features_df["subject_id"] = features_df["subject_id"].astype(str)
    fusion = pd.merge(cohort_df, features_df, on="subject_id", how="left")
    for organ in organs:
        organ_cols = [c for c in fusion.columns if c.startswith(f"{organ}__")]
        if organ_cols:
            fusion[f"{organ}_present"] = fusion[organ_cols[0]].notna().astype(int)
        else:
            fusion[f"{organ}_present"] = 0
    return fusion


def build_availability_audit(fusion_table, organs):
    records = []
    for _, row in fusion_table.iterrows():
        subject_id = row["subject_id"]
        for organ in organs:
            present = row.get(f"{organ}_present", 0)
            records.append({
                "subject_id": subject_id,
                "organ": organ,
                "present": present,
                "status": "extracted" if present else "missing"
            })
    return pd.DataFrame(records)


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
    # Deprecated and ignored since the database-backed flow: target labels come from the
    # approved cohort's members, never from a targets.csv on disk.
    targets_csv: WorkspacePath = Field(default=None)
    id_column: Optional[str] = Field(default=None)
    target_column: Optional[str] = Field(default=None)
    id_pattern: Optional[str] = Field(default=None)
    image_dir: WorkspacePath = Field(default=None)
    mask_dir: WorkspacePath = Field(default=None)
    output_dir: WorkspacePath = Field(default=None)
    require_all_organs: bool = Field(default=False)


class AnalyzeImportanceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="ignore")

    input_path: WorkspacePath = Field(
        default=None,
        description=(
            "A generic CSV to select from. Omit it — the normal case — and the tool "
            "selects from the radiomics rows this run recorded."
        ),
    )
    top_features: Union[int, str, List[Union[int, str]]] = Field(default="10")
    task_type: Optional[str] = Field(default=None)
    method: str = Field(default="rf")
    encode_categorical: str = Field(default="auto")
    max_onehot_cardinality: int = Field(default=10)
    create_plots: bool = Field(default=True)
    top_n_plot: int = Field(default=30)
    holdout_size: float = Field(
        default=0.2,
        description=(
            "Fraction of subjects excluded from feature selection so their labels do "
            "not leak into the selected set. Set to 0 to disable, which makes the "
            "selection label-supervised on every row and is recorded as such."
        ),
    )
    split_seed: int = Field(default=42, description="Seed for the selection holdout split.")
    group_column: Optional[str] = Field(
        default=None,
        description=(
            "Ignored since V1 step I: the split is always grouped by subject_key, the "
            "cohort's patient-level split group, so a patient's rows never straddle it."
        ),
    )


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
    """Extract radiomic features from medical images and segmentation masks using PyRadiomics."""
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

    # The PyRadiomics parameter file and the tool log are disposable
    # diagnostics: they live under temp/, and the measurements are
    # rows in PostgreSQL. `output_dir` is accepted and ignored.
    output_dir = str(TEMP_DIR / "feature-extraction" / (domain_api.agent_run_id() or "unbound"))

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

    target_column = 'target'

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

        # ADDED FOR UI ENHANCEMENT — Task 1: Dynamic bin_width tuning.
        # Samples up to 5 NIfTI headers from image_dir to detect the acquisition
        # modality (MRI vs CT) and pick an appropriate intensity histogram bin width.
        # CT uses standardised Hounsfield Units so the default 25.0 HU is correct.
        # MRI has an arbitrary intensity scale — using 25.0 would over-bin most images.
        # Safety: any nibabel / glob error → falls back to params.bin_width unchanged.
        current_bin_width: float = params.bin_width
        try:
            import nibabel as _nib
            _bw_samples = sorted(_glob.glob(os.path.join(image_dir, params.image_pattern)))[:5]
            _intensities_min: list = []
            _intensities_max: list = []
            for _sf in _bw_samples:
                try:
                    _arr = _nib.load(_sf).get_fdata(dtype="float32")
                    _intensities_min.append(float(_arr.min()))
                    _intensities_max.append(float(_arr.max()))
                except Exception:
                    pass
            if _intensities_min and _intensities_max:
                _global_min = min(_intensities_min)
                _global_max = max(_intensities_max)
                _intensity_range = _global_max - _global_min
                # Heuristic: CT HU range typically spans >3000 units;
                # MRI intensities are narrower and do not include large negatives.
                if abs(_global_min) < 1500 and _global_max < 5000 and _intensity_range < 3000:
                    # MRI-like: derive bin_width from actual intensity range / 128 bins
                    _adaptive_bw = max(0.5, _intensity_range / 128.0)
                    current_bin_width = round(_adaptive_bw, 2)
                    logger.info(
                        "[Task 1] MRI-like intensity range detected "
                        f"({_global_min:.1f}\u2013{_global_max:.1f}). "
                        f"Using adaptive bin_width={current_bin_width} (was {params.bin_width})."
                    )
                else:
                    # CT-like: keep user-supplied bin_width (default 25.0 HU)
                    logger.info(
                        "[Task 1] CT-like intensity range detected "
                        f"({_global_min:.1f}\u2013{_global_max:.1f}). "
                        f"Keeping bin_width={current_bin_width}."
                    )
        except ImportError:
            logger.info("[Task 1] nibabel not installed \u2014 using default bin_width.")
        except Exception as _bw_exc:
            logger.warning(f"[Task 1] bin_width auto-detect failed: {_bw_exc} \u2014 using default.")

        pyrad_params: dict = {
            "imageType": {},
            "featureClass": {},
            "setting": {
                "force2D": params.force_2d,
                "normalize": params.normalize,
                "normalizeScale": 100,
                "binWidth": current_bin_width,   # TASK 1: was params.bin_width
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

        # ── Extraction identity ────────────────────────────────────────
        # The configuration digest covers the extractor version and every
        # setting — the adaptive bin width included — so a changed setting is a
        # different content-addressed row, never a reused earlier one.
        import radiomics as _radiomics

        extractor_name = "pyradiomics"
        extractor_version = str(getattr(_radiomics, "__version__", "unknown"))
        extraction_configuration = {
            "schema_version": 1,
            "pyradiomics": pyrad_params,
            # One reviewed pair is one target on one SEG segment.
            "mask_label_policy": "pair_segment",
            # A null feature value means the extractor returned NaN/Infinity.
            "missing_value_policy": "non_finite_to_null",
        }
        extraction_config_digest = provenance.config_digest(
            extractor_name, extractor_version, extraction_configuration
        )

        # ── Target labels: the approved cohort ────────────
        # `populations/targets/targets.csv` is gone. The labels are the frozen
        # cohort's labelled members, in ordinal order; a member with no label
        # is pending/excluded, never a negative.
        def _normalise_id(value: Any) -> str:
            value = str(value).strip().casefold()
            for extension in (".nii.gz", ".nii"):
                if value.endswith(extension):
                    return value[:-len(extension)]
            return value

        try:
            # Unbound tools cannot record rows; fail before any extraction work.
            run_id = domain_api.require_run()
            cohort = await domain_api.require_population_cohort()
        except domain_api.DomainApiError as exc:
            return json.dumps({"status": "error", "error_message": str(exc)}, indent=2)
        if not cohort.get("frozen_at"):
            return json.dumps({
                "status": "error",
                "error_message": (
                    "The population cohort has not been approved. Feature extraction "
                    "requires the doctor-approved cohort's labels."
                ),
            }, indent=2)
        analysis_rows = await domain_api.cohort_analysis_rows(cohort["cohort_id"])
        if not analysis_rows:
            return json.dumps({
                "status": "error",
                "error_message": "The approved cohort has no labelled volume.",
            }, indent=2)

        target_dict = {
            _normalise_id(row["volume_series_instance_uid"]): row["target"]
            for row in analysis_rows
        }
        cohort_id = str(cohort["cohort_id"])
        subject_of_volume = {
            str(row["volume_series_instance_uid"]): str(row["subject_key"])
            for row in analysis_rows
        }
        logger.info(
            "Loaded %d labelled cohort member(s) from cohort %s", len(target_dict), cohort_id
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

        # ── Reviewed masks: segmentation_pairs ─────
        # Every radiomics row names the SEG and segment it measured (
        # item 5), and a pipeline mask is not usable until its review decision
        # is set. So only the accepted and modified pairs of a submitted review
        # are measured. The old directory-matching fallback — masks with no SEG
        # identity — is gone: it could only produce unattributable numbers.
        pairs: dict[str, dict[str, Any]] = {}
        for pair in await domain_api.reviewed_masks():
            subject_id = str(pair.get("volume_series_instance_uid") or "")
            target_id = str(pair.get("target_key") or "")
            pair_id = str(pair.get("pair_key") or "")
            label_uid = str(pair.get("selected_segmentation_uid") or "")
            mask_rel = str((pair.get("quality") or {}).get("mask_path") or "")
            image_path = images_dict.get(subject_id)
            mask_path = base_on_workspace_dir(mask_rel) if mask_rel else None
            if not (subject_id and target_id and pair_id and label_uid and image_path and mask_path):
                logger.warning(
                    "Reviewed pair %s has no image or mask on disk, or no selected SEG; skipping",
                    pair_id or subject_id,
                )
                continue
            if not os.path.isfile(mask_path):
                logger.warning("Mask %s of reviewed pair %s is missing; skipping", mask_path, pair_id)
                continue
            pairs[pair_id] = {
                "image": image_path,
                "mask": mask_path,
                "subject_id": subject_id,
                "target_id": target_id,
                "segmentation_pair_id": str(pair.get("pair_id") or ""),
                "label_series_instance_uid": label_uid,
                "segment_number": int(pair.get("segment_number") or 1),
            }
        logger.info("Loaded %d reviewed segmentation mask(s)", len(pairs))

        if not pairs:
            return json.dumps({
                "status": "error",
                "error_message": (
                    "No reviewed segmentation mask is available to measure. Feature "
                    "extraction measures the accepted or modified masks of a submitted "
                    "segmentation review; submit the review first."
                ),
            }, indent=2)

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
                    "Check that the image files are named by the cohort's volume UIDs, or provide id_pattern."
                ),
                "unlabeled_subject_ids": unlabeled_subject_ids,
            }, indent=2)
        if unlabeled_subject_ids:
            logger.warning("Skipping %s image-mask pair(s) with no target label: %s", len(unlabeled_subject_ids), unlabeled_subject_ids)

        # `mask_labels`, when given, only restricts which label values of a
        # pair's mask file may be measured; the segment a row records is always
        # the pair's own SEG segment.
        requested_labels = set(mask_labels_raw) if mask_labels_raw else None
        labels_used: set[int] = set()
        radiomics_rows: list[dict[str, Any]] = []
        row_targets: dict[tuple, str] = {}
        attempted: set[tuple[str, str]] = set()
        image_digests: dict[str, str] = {}
        radiology_temp_dir = TEMP_DIR / "radiology"
        radiology_temp_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = tempfile.mkdtemp(dir=radiology_temp_dir)
        start = time.time()

        # ADDED FOR UI ENHANCEMENT — Task 5: GPU detection (informational).
        # PyRadiomics runs on SimpleITK which has no native CUDA path, so the
        # ProcessPoolExecutor below is always used regardless of GPU presence.
        # GPU availability is logged and reported in the summary dict (Task 7)
        # for observability. Zero change to the extraction executor.
        gpu_available: bool = False
        try:
            import torch as _torch
            gpu_available = bool(_torch.cuda.is_available())
            if gpu_available:
                _gpu_name = _torch.cuda.get_device_name(0)
                logger.info(
                    f"[Task 5] GPU detected: {_gpu_name}. "
                    "Note: PyRadiomics/SimpleITK does not use CUDA for radiomic feature "
                    "computation. ProcessPoolExecutor (CPU) is used. GPU reported in "
                    "summary for observability."
                )
            else:
                logger.info("[Task 5] No CUDA GPU detected. Using CPU ProcessPoolExecutor.")
        except ImportError:
            logger.debug("[Task 5] torch not installed \u2014 GPU detection skipped.")
        except Exception as _gpu_exc:
            logger.debug(f"[Task 5] GPU detection error: {_gpu_exc}")

        # ADDED FOR UI ENHANCEMENT — Tasks 2 & 3: Accumulator for per-subject
        # spatial warnings and mask-too-small reasons. Merged into the audit CSV
        # after the extraction loop completes.
        spatial_warnings: dict[str, str] = {}

        payload = TextPayload(message=f"Extracting features for {len(pairs)} masks...")
        log_tool_state(ToolEvent("tool_started", "text", payload))
        for idx, (pair_id, files) in enumerate(pairs.items(), 1):
            subject_id = files["subject_id"]
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
                attempted.add((subject_of_volume.get(subject_id, subject_id), target_id))
                # Decoded content, not file bytes: a re-converted NIfTI
                # of the same series digests identically.
                if files["image"] not in image_digests:
                    image_digests[files["image"]] = provenance.image_digest(orig_image)
                content_image_digest = image_digests[files["image"]]

                # One pair is one target on one SEG segment. A single-label
                # mask file is that segment; a multi-label file is measured only
                # on the label equal to the pair's segment number.
                present_labels = sorted(int(value) for value in np.unique(mask_array) if value > 0)
                if len(present_labels) == 1:
                    label_plan = [(present_labels[0], files["segment_number"])]
                elif files["segment_number"] in present_labels:
                    label_plan = [(files["segment_number"], files["segment_number"])]
                else:
                    label_plan = []
                    logger.warning(
                        "  %s: mask labels %s do not identify segment %s of %s; skipping",
                        subject_id, present_labels, files["segment_number"],
                        files["label_series_instance_uid"],
                    )
                if requested_labels is not None:
                    label_plan = [(lbl, seg) for lbl, seg in label_plan if lbl in requested_labels]

                # ADDED FOR UI ENHANCEMENT — Task 3: Spatial alignment check.
                # Compares affine matrices of volume and mask using nibabel.
                # Mismatch is recorded in spatial_warnings and later written to
                # the audit CSV. Extraction always proceeds regardless.
                if _HELPERS_AVAILABLE:
                    try:
                        _aligned, _sw_msg = check_spatial_alignment(files["image"], files["mask"])
                        if not _aligned and _sw_msg:
                            spatial_warnings[subject_id] = _sw_msg
                            logger.warning(f"  [Task 3] {subject_id}: {_sw_msg}")
                    except Exception as _sa_exc:
                        logger.debug(f"  [Task 3] Spatial check error for {subject_id}: {_sa_exc}")

                # ADDED FOR UI ENHANCEMENT — Task 4: Cache directory.
                _cache_dir = str(CACHE_DIR / "monailabel" / "feature-extraction")

                for lbl, segment_number in label_plan:
                    binary = (mask_array == lbl).astype(np.uint8)
                    if binary.sum() == 0:
                        continue

                    # ADDED FOR UI ENHANCEMENT — Task 2: Mask voxel count guard.
                    # PyRadiomics produces NaN-only rows or crashes when the segmented
                    # region is degenerate (< 10 voxels). Skip and audit the reason.
                    if _HELPERS_AVAILABLE:
                        _mask_valid, _voxel_count = check_mask_voxels(binary)
                    else:
                        _voxel_count = int(binary.sum())
                        _mask_valid  = _voxel_count >= 10
                    if not _mask_valid:
                        _reason = f"Mask_too_small (label {lbl}, {_voxel_count} voxels < 10)"
                        logger.warning(f"  [Task 2] {subject_id}: {_reason} — skipping extraction.")
                        # Record in spatial_warnings so it appears in the audit CSV.
                        # Only set if no stronger spatial warning already exists.
                        spatial_warnings.setdefault(subject_id, _reason)
                        continue

                    bin_img = sitk.GetImageFromArray(binary)
                    bin_img.CopyInformation(orig_mask)
                    tmp_mask_path = os.path.join(temp_dir, f"{subject_id}_lbl{lbl}.nii.gz")
                    sitk.WriteImage(bin_img, tmp_mask_path)
                    try:
                        import multiprocessing

                        # ADDED FOR UI ENHANCEMENT — Task 4: Cache lookup.
                        # If we have already extracted features for this exact
                        # image+mask+bin_width combination, load from disk instead
                        # of re-running the expensive subprocess.
                        _features: Optional[dict] = None
                        _cache_key: str = ""
                        content_segmentation_digest = provenance.segmentation_digest(
                            binary,
                            orig_mask,
                            label_series_instance_uid=files["label_series_instance_uid"],
                            segment_number=segment_number,
                        )
                        content_source_digest = provenance.source_digest(
                            image_digest=content_image_digest,
                            segmentation_digest=content_segmentation_digest,
                            config_digest=extraction_config_digest,
                            label_series_instance_uid=files["label_series_instance_uid"],
                            segment_number=segment_number,
                        )
                        if _HELPERS_AVAILABLE:
                            # Content-addressed: a changed setting or intensity
                            # misses; paths and file times are not the key.
                            _cache_key = get_cache_key(
                                content_image_digest,
                                content_segmentation_digest,
                                extraction_config_digest,
                            )
                            _features = load_from_cache(_cache_dir, _cache_key)
                            if _features is not None:
                                logger.info(
                                    f"  [Task 4] Cache HIT: {subject_id} label {lbl} "
                                    f"(key {_cache_key[:8]}…)"
                                )

                        if _features is None:
                            # ── Full extraction via isolated subprocess (original logic) ──
                            ctx = multiprocessing.get_context('spawn')
                            _n_workers = max(1, min(params.n_workers, os.cpu_count() or 1))
                            executor = concurrent.futures.ProcessPoolExecutor(
                                max_workers=_n_workers, mp_context=ctx
                            )
                            try:
                                future = executor.submit(
                                    _run_extractor, files["image"], tmp_mask_path, params_file
                                )
                                _features = await asyncio.to_thread(
                                    future.result,
                                    FEATURE_EXTRACTION_TIMEOUT_SECONDS,
                                )
                            except (
                                concurrent.futures.TimeoutError,
                                asyncio.CancelledError,
                            ):
                                _terminate_process_pool(executor)
                                executor = None
                                raise
                            finally:
                                if executor is not None:
                                    await asyncio.to_thread(
                                        executor.shutdown,
                                        wait=True,
                                        cancel_futures=True,
                                    )

                            # ADDED FOR UI ENHANCEMENT — Task 4: Cache save.
                            if _HELPERS_AVAILABLE and _cache_key:
                                save_to_cache(_cache_dir, _cache_key, _features)

                        # One provenance row per measured segment. A
                        # non-finite value becomes null, and the configuration's
                        # missing_value_policy says what a null means.
                        measured = {
                            str(name): finite_or_none(value)
                            for name, value in (_features or {}).items()
                        }
                        if not measured:
                            logger.error(f"  {subject_id} label {lbl}: extractor returned no features")
                            continue
                        radiomics_rows.append({
                            "label_series_instance_uid": files["label_series_instance_uid"],
                            "source_volume_uid": subject_id,
                            "segment_number": int(segment_number),
                            "segmentation_pair_id": files["segmentation_pair_id"] or None,
                            "features": measured,
                            "extractor_name": extractor_name,
                            "extractor_version": extractor_version,
                            "configuration": extraction_configuration,
                            "config_digest": extraction_config_digest,
                            "image_digest": content_image_digest,
                            "segmentation_digest": content_segmentation_digest,
                            "source_digest": content_source_digest,
                            "schema_version": 1,
                        })
                        row_targets[(
                            files["label_series_instance_uid"],
                            int(segment_number),
                            content_source_digest,
                        )] = target_id
                        labels_used.add(int(lbl))
                    except concurrent.futures.TimeoutError:
                        logger.error(
                            f"  {subject_id} label {lbl}: extraction timed out after "
                            f"{FEATURE_EXTRACTION_TIMEOUT_SECONDS:.0f}s; skipping"
                        )
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

        if not radiomics_rows:
            return json.dumps({
                "status": "error",
                "error_message": "No radiomic features were extracted from any reviewed mask.",
                "paired_subjects": paired_subject_count,
                "labeled_subjects": len(pairs),
            }, indent=2)

        # ── Record every row ─────────────────────────────────────────
        # A PostgreSQL write failure fails the operation. There is no file
        # to fall back to, and an extraction that leaves radiomics_features
        # empty has not happened. Each batch is all-or-nothing and replays onto
        # the content-addressed key, so a retry after a crash reuses rows.
        recorded = {"created": 0, "reused": 0}
        recorded_rows: list[dict[str, Any]] = []
        try:
            for offset in range(0, len(radiomics_rows), domain_api.RADIOMICS_BATCH_SIZE):
                batch = radiomics_rows[offset: offset + domain_api.RADIOMICS_BATCH_SIZE]
                result = await domain_api.record_radiomics_rows(batch)
                recorded["created"] += int(result["created"])
                recorded["reused"] += int(result["reused"])
                ids = {
                    (item["label_series_instance_uid"], int(item["segment_number"]), item["source_digest"]): item["id"]
                    for item in result["rows"]
                }
                for row in batch:
                    key = (row["label_series_instance_uid"], int(row["segment_number"]), row["source_digest"])
                    recorded_rows.append({**row, "id": ids[key], "target_key": row_targets.get(key)})
        except domain_api.DomainApiError as exc:
            return json.dumps({
                "status": "error",
                "error_message": f"Radiomics rows could not be recorded: {exc}",
                "recorded_before_failure": recorded,
            }, indent=2)
        radiomics_summary = {**recorded, "total": len(recorded_rows)}

        # ── Summary, from the rows exactly as selection will read them ──────
        try:
            table = wide_table_from_rows(recorded_rows, analysis_rows)
        except FeatureSetBuildError as exc:
            return json.dumps({
                "status": "error",
                "error_message": (
                    f"Radiomics rows were recorded, but they cannot form a feature set: {exc}"
                ),
                "radiomics_rows": radiomics_summary,
            }, indent=2)
        frame = table.frame
        audit_records: list[dict[str, Any]] = []
        for record in frame.to_dict("records"):
            for organ in table.organs:
                present = int(record.get(f"{organ}_present", 0))
                if present:
                    status = "extracted"
                elif (record[SUBJECT_KEY], organ) in attempted:
                    status = "extraction_failed_or_empty"
                else:
                    status = "segmentation_missing"
                audit_records.append({
                    "subject_key": record[SUBJECT_KEY],
                    "organ": organ,
                    "present": present,
                    "status": status,
                    "spatial_warning": spatial_warnings.get(record[SUBJECT_KEY], ""),
                })
        missing_required = [item for item in audit_records if not item["present"]]
        if params.require_all_organs and missing_required:
            return json.dumps({
                "status": "error",
                "error_message": (
                    f"{len(missing_required)} required patient-organ combinations are missing"
                ),
                "radiomics_rows": radiomics_summary,
                "missing_patient_organs": missing_required[:200],
            }, indent=2)

        num_features = {
            organ: len([
                column for column in table.feature_columns
                if column.startswith(f"{organ}__") or column.startswith(f"{organ}_label_")
            ])
            for organ in table.organs
        }
        volume_columns = [column for column in table.feature_columns if "voxelvolume" in column.lower()]
        volume_range: list = []
        if volume_columns:
            volumes = pd.to_numeric(frame[volume_columns[0]], errors="coerce").dropna()
            if not volumes.empty:
                volume_range = [round(float(volumes.min()), 2), round(float(volumes.max()), 2)]
        subjects_with_features = int(sum(
            1 for record in frame.to_dict("records")
            if any(record.get(f"{organ}_present") for organ in table.organs)
        ))
        summary: dict = {
            "total_patients": int(len(frame)),
            "successful_extractions": subjects_with_features,
            "failed_count": len(missing_required),
            "failed_patient_ids": [item["subject_key"] for item in missing_required][:50],
            "patients_with_spatial_warnings": list(spatial_warnings.keys()),
            "volume_range": volume_range,
            "extraction_params_used": {
                "bin_width": current_bin_width,
                "image_types": image_types,
                "feature_classes": feature_classes or [],
                "config_digest": extraction_config_digest,
                "extractor": f"{extractor_name} {extractor_version}",
            },
            "gpu_available": gpu_available,
        }

        return json.dumps({
            "status": "success",
            "agent_run_id": run_id,
            "cohort_id": cohort_id,
            # The measurements are rows; this is their count, and the
            # export below is generated from them on request.
            "radiomics_rows": radiomics_summary,
            "exports": {"radiomics_features": f"/radiomics/run/{run_id}/export"},
            "num_subjects": subjects_with_features,
            "num_volume_target_pairs": len(pairs),
            "paired_subjects": paired_subject_count,
            "unlabeled_subject_ids": unlabeled_subject_ids,
            "num_features": num_features,
            "mask_labels": sorted(labels_used),
            "processing_time_seconds": round(processing_time, 2),
            "summary": summary,
            "next_step": (
                "Call radiomics_analyze_importance with no input_path; it selects from "
                "the rows this run recorded and freezes the feature sets."
            ),
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
    """Perform feature importance analysis. Returns chart_data for frontend rendering."""
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

    # Plots are rendered here and published as artifacts; nothing under this
    # directory is authoritative.
    output_dir = str(TEMP_DIR / "feature-selection" / (domain_api.agent_run_id() or "unbound"))
    os.makedirs(output_dir, exist_ok=True)
    log_file = os.path.join(output_dir, "feature_importance_analysis.log")
    _setup_logging(log_file)
    logger = logging.getLogger(__name__)

    target_column = 'target'

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
        source_label = (
            os.path.basename(params.input_path) if params.input_path else "this run's radiomics rows"
        )
        payload = TextPayload(message=f"Analysing feature importance for {source_label}...")
        log_tool_state(ToolEvent("tool_started", "text", payload))

        # ── Input: this run's radiomics rows, labelled from the cohort ──────
        # . The label is the frozen cohort member's; a generic CSV's
        # own label column is dropped, so only one copy of a label exists.
        run_id = domain_api.require_run()
        cohort = await domain_api.require_population_cohort()
        if not cohort.get("frozen_at"):
            raise ValueError(
                "The population cohort has not been approved; feature selection needs "
                "its frozen labels."
            )
        analysis_rows = await domain_api.cohort_analysis_rows(cohort["cohort_id"])
        input_notes: list = []
        radiomics_config_digests: list = []
        if params.input_path:
            if not os.path.exists(params.input_path):
                raise ValueError(f"Input file not found: {params.input_path}")
            with open(params.input_path, "rb") as fh:
                input_bytes = fh.read()
            df, input_notes = frame_from_csv(pd.read_csv(io.BytesIO(input_bytes)), analysis_rows)
            source_feature_ids = None
            # The exact input bytes are the source identity of a CSV-backed set.
            source_digest = provenance.bytes_digest(input_bytes)
            source_kind = "input_csv"
        else:
            recorded_rows = await domain_api.run_radiomics_rows(run_id)
            if not recorded_rows:
                raise ValueError(
                    "This run has recorded no radiomics rows; call radiomics_extract_features first."
                )
            table = wide_table_from_rows(recorded_rows, analysis_rows)
            df = table.frame
            source_feature_ids = table.source_feature_ids
            # Recomputed by the Application API from the rows' own digests.
            source_digest = None
            source_kind = "radiomics_features"
            radiomics_config_digests = sorted(
                {row["config_digest"] for row in recorded_rows if row.get("config_digest")}
            )
        subject_keys = df[SUBJECT_KEY].astype(str).tolist()

        X = df.drop(columns=[target_column])
        id_cols_to_drop = [
            c for c in X.columns
            if c.lower() in ("id", "subject_id", "subjectid", "subject_key", "ordinal", "patientid", "patient_id", "case_id", "caseid", "name", "uid", "pat_id", "unnamed: 0")
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

        # ── Plan the selection holdout BEFORE anything is fitted ───────────
        # Every importance method below fits on (X, y). Fitting on all rows leaks the
        # holdout labels into the selected feature set, and PyCaret's later split
        # cannot undo that. Plan the split here and fit only on `train_pos`.
        # The split group is always the cohort's subject_key:
        # a patient's rows never straddle the holdout, and the stored
        # holdout_groups are subject keys the modeling export can split on.
        if params.group_column and params.group_column != SUBJECT_KEY:
            logger.warning(
                "group_column=%r ignored: the selection split is grouped by subject_key.",
                params.group_column,
            )
        group_column = SUBJECT_KEY
        split = plan_selection_split(
            n_rows=len(df),
            labels=y.tolist(),
            group_values=subject_keys,
            group_column=group_column,
            task_type=task_type,
            holdout_size=params.holdout_size,
            seed=params.split_seed,
        )
        if split.label_supervised_on_full_data:
            logger.warning(
                "Feature selection is fitted on every row (%s). Reported holdout "
                "metrics downstream are optimistic; this is recorded in the output.",
                split.degraded_reason,
            )
        else:
            logger.info(
                "Selection holdout: %d of %d rows (%s, %d groups, seed=%d) excluded "
                "from importance fitting.",
                len(split.holdout_index), len(df), split.strategy,
                split.n_groups, split.seed,
            )
        train_pos = split.train_index

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
                    # Fitted on training rows only: a target encoding fitted on the
                    # holdout writes those labels straight into a feature column.
                    tmp = pd.concat([X[[col]], y], axis=1).iloc[train_pos]
                    means = tmp.groupby(col)[target_column].mean()
                    X[f"{col}_target"] = X[col].map(means)
                    freqs = X[col].iloc[train_pos].value_counts(normalize=True)
                    X[f"{col}_freq"]   = X[col].map(freqs)
                    encoding_mappings[col] = {"method": "target", "fitted_on": "train"}
            X = X.drop(columns=cat_cols)
        elif params.encode_categorical == "none" and cat_cols:
            X = X.select_dtypes(include=[np.number])

        num_cols = X.select_dtypes(include=[np.number]).columns
        # Medians come from the training rows only; a median over the holdout is a
        # summary of held-out data leaking back into every imputed cell.
        X[num_cols] = X[num_cols].fillna(X[num_cols].iloc[train_pos].median())
        X = X.fillna(0)

        # The encodings are recorded in each feature set's extraction_config.

        # Everything that consumes labels fits on these; plots and exports still use
        # the full X so the UI and the downstream CSVs keep every row.
        X_fit = X.iloc[train_pos]
        y_fit = y.iloc[train_pos]

        logger.info(
            f"Feature matrix: {X.shape[0]} rows × {X.shape[1]} features "
            f"(importance fitted on {X_fit.shape[0]} row(s))"
        )

        payload = ProgressPayload(current=1, total=TOTAL_STAGES, label="Loading & preprocessing data")
        log_tool_state(ToolEvent("tool_progress", "progress", payload))

        # ── Stage 2: Importance computation ────────────────────────────────
        def _rf_importance(n):
            clf = (RandomForestClassifier if task_type == "classification"
                   else RandomForestRegressor)(n_estimators=100, random_state=42)
            clf.fit(X_fit, y_fit)
            imp = clf.feature_importances_
            idx = np.argsort(imp)[::-1][:n]
            return X.columns[idx].tolist(), imp

        def _f_test_importance(n):
            fn = f_classif if task_type == "classification" else f_regression
            sel = SelectKBest(fn, k=min(n, X.shape[1])).fit(X_fit, y_fit)
            return X.columns[sel.get_support()].tolist(), sel.scores_

        def _mutual_info_importance(n):
            fn = mutual_info_classif if task_type == "classification" else mutual_info_regression
            sel = SelectKBest(fn, k=min(n, X.shape[1])).fit(X_fit, y_fit)
            return X.columns[sel.get_support()].tolist(), sel.scores_

        def _rfe_importance(n):
            est = (RandomForestClassifier if task_type == "classification"
                   else RandomForestRegressor)(n_estimators=100, random_state=42)
            sel = RFE(est, n_features_to_select=min(n, X.shape[1]), step=0.1).fit(X_fit, y_fit)
            # Lower RFE rankings are better; negate them so the common
            # descending sort below retains the selected features first.
            return X.columns[sel.get_support()].tolist(), -sel.ranking_

        method_fn = {"rf": _rf_importance, "f_test": _f_test_importance,
                     "mutual_info": _mutual_info_importance, "rfe": _rfe_importance}[params.method]

        all_feats, scores = method_fn(X.shape[1])
        importance_df = pd.DataFrame({"feature": X.columns, "importance": scores})
        importance_df = importance_df.sort_values("importance", ascending=False).reset_index(drop=True)

        payload = ProgressPayload(current=2, total=TOTAL_STAGES, label=f"Computing importance ({params.method}, {task_type})")
        log_tool_state(ToolEvent("tool_progress", "progress", payload))

        # ADDED FOR UI ENHANCEMENT — Task 6: Alternative importance rankings.
        # Compute ANOVA F-test and Mutual Information rankings in addition to the
        # primary method. These live in a separate top-level key (alternative_rankings)
        # so chart_data.importance_bars is NEVER modified — zero UI breaking change.
        # If any sub-computation fails the exception is caught and that ranking is
        # simply omitted from the response.
        alternative_rankings: dict = {}
        stability_scores: list = []
        try:
            if X_fit.shape[0] >= 5 and X.shape[1] >= 2:
                # ── ANOVA (F-test) ────────────────────────────────────────────────
                try:
                    _fn_anova = f_classif if task_type == "classification" else f_regression
                    _anova_scores = SelectKBest(_fn_anova, k="all").fit(X_fit, y_fit).scores_
                    _anova_df = (
                        pd.DataFrame({"feature": X.columns, "importance": _anova_scores})
                        .sort_values("importance", ascending=False)
                        .reset_index(drop=True)
                    )
                    alternative_rankings["anova"] = [
                        {"feature": str(r["feature"]), "importance": _safe(r["importance"])}
                        for _, r in _anova_df.head(min(params.top_n_plot, len(_anova_df))).iterrows()
                    ]
                    logger.info("[Task 6] ANOVA rankings computed.")
                except Exception as _anova_exc:
                    logger.warning(f"[Task 6] ANOVA ranking failed: {_anova_exc}")

                # ── Mutual Information ────────────────────────────────────────────
                try:
                    _fn_mi = mutual_info_classif if task_type == "classification" else mutual_info_regression
                    _mi_scores = SelectKBest(_fn_mi, k="all").fit(X_fit, y_fit).scores_
                    _mi_df = (
                        pd.DataFrame({"feature": X.columns, "importance": _mi_scores})
                        .sort_values("importance", ascending=False)
                        .reset_index(drop=True)
                    )
                    alternative_rankings["mutual_info"] = [
                        {"feature": str(r["feature"]), "importance": _safe(r["importance"])}
                        for _, r in _mi_df.head(min(params.top_n_plot, len(_mi_df))).iterrows()
                    ]
                    logger.info("[Task 6] Mutual Info rankings computed.")
                except Exception as _mi_exc:
                    logger.warning(f"[Task 6] Mutual Info ranking failed: {_mi_exc}")

                # ── Feature Stability: CV proxy (lightweight alternative to ICC) ──
                # Coefficient of Variation (σ / |μ|) per feature.
                # Lower CV = more stable across patients. stability = 1 / (1 + CV).
                try:
                    _cv = (X.std() / (X.mean().abs() + 1e-9)).abs()
                    _stab_df = (
                        pd.DataFrame({"feature": X.columns, "cv": _cv})
                        .assign(stability=lambda d: 1.0 / (1.0 + d["cv"]))
                        .sort_values("stability", ascending=False)
                        .reset_index(drop=True)
                    )
                    stability_scores = [
                        {
                            "feature": str(r["feature"]),
                            "stability": round(float(r["stability"]), 4),
                        }
                        for _, r in _stab_df.head(min(params.top_n_plot, len(_stab_df))).iterrows()
                    ]
                    logger.info("[Task 6] Stability scores (CV proxy) computed.")
                except Exception as _cv_exc:
                    logger.warning(f"[Task 6] Stability scores failed: {_cv_exc}")

                # ── Volume-normalised importance ──────────────────────────────────
                # Re-rank features after regressing out shape_VoxelVolume, revealing
                # biologically-significant features beyond lesion size alone.
                try:
                    _vol_cols = [c for c in X.columns if "voxelvolume" in c.lower()]
                    if _vol_cols:
                        from sklearn.linear_model import LinearRegression as _LR
                        _vol_arr = X[_vol_cols].values
                        _vol_arr_fit = X_fit[_vol_cols].values
                        X_resid = X.copy()
                        for _col in X.columns:
                            try:
                                # Residualisation is unsupervised, but fitting it on
                                # every row still lets holdout structure shape the
                                # feature the classifier below is scored on.
                                _lr = _LR().fit(_vol_arr_fit, X_fit[_col].values)
                                X_resid[_col] = X[_col].values - _lr.predict(_vol_arr)
                            except Exception:
                                pass  # keep original values for this column
                        _clf_vol = (
                            RandomForestClassifier if task_type == "classification"
                            else RandomForestRegressor
                        )(n_estimators=50, random_state=42)
                        _clf_vol.fit(X_resid.iloc[train_pos], y_fit)
                        _vol_imp_df = (
                            pd.DataFrame({"feature": X.columns, "importance": _clf_vol.feature_importances_})
                            .sort_values("importance", ascending=False)
                            .reset_index(drop=True)
                        )
                        alternative_rankings["normalized_by_volume"] = [
                            {"feature": str(r["feature"]), "importance": _safe(r["importance"])}
                            for _, r in _vol_imp_df.head(min(params.top_n_plot, len(_vol_imp_df))).iterrows()
                        ]
                        logger.info("[Task 6] Volume-normalised importance computed.")
                except Exception as _vol_exc:
                    logger.warning(f"[Task 6] Volume-normalised importance failed: {_vol_exc}")

        except Exception as _alt_exc:
            logger.warning(f"[Task 6] Alternative rankings outer error: {_alt_exc}")
            alternative_rankings = {}
            stability_scores = []

        # ── Build chart_data ────────────────────────────────────────────────
        chart_data: dict = {"task_type": task_type, "method": params.method}
        # importance_df already sorted descending above; no re-sort needed here.

        # The ranking is label-derived: it goes back to the agent (chart_data,
        # top_features) and is never stored as feature-set analysis.

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

        # Retained figures are artifacts — bytes in PostgreSQL — so the report
        # binds them by key instead of scanning the workspace.
        selection_warnings: list = []
        figures: list = []
        figure_names = sorted(os.listdir(plots_dir)) if os.path.isdir(plots_dir) else []
        for figure_name in figure_names:
            if not figure_name.lower().endswith(".png"):
                continue
            with open(os.path.join(plots_dir, figure_name), "rb") as fh:
                figure_bytes = fh.read()
            if not figure_bytes:
                continue
            try:
                artifact = await domain_api.publish_artifact(
                    logical_key=f"features/{run_id}/{figure_name}",
                    kind="figure_png",
                    payload=figure_bytes,
                    filename=figure_name,
                    mime_type="image/png",
                    metadata={
                        "schema_version": 1,
                        "producer": "radiomics_analyze_importance",
                        "method": params.method,
                    },
                )
            except domain_api.DomainApiError as exc:
                if exc.status_code != 409:
                    raise
                # A replay rendered different bytes under the same key: the
                # earlier figure stays (bytes are immutable) and this is said.
                selection_warnings.append(f"{figure_name}: kept the earlier published figure ({exc})")
                continue
            figures.append({
                "artifact_id": str(artifact["artifact_id"]),
                "logical_key": artifact["logical_key"],
                "display_filename": artifact.get("display_filename"),
            })

        # ── Stage 8: Freeze the feature sets ────────────────────
        # One frozen set per requested top-N plus every feature
        # ("all_features"), so modeling can train on both. Each carries the
        # selection manifest; train/holdout are reconstructed from its
        # holdout_groups at export time and never stored as CSVs.
        split_manifest = split.to_manifest()
        selection = selection_document(split_manifest)
        base_config = {
            "schema_version": FEATURE_SET_SCHEMA_VERSION,
            "source": source_kind,
            "radiomics_config_digests": radiomics_config_digests,
            "selection_method": params.method,
            "task_type": task_type,
            "encode_categorical": params.encode_categorical,
            "max_onehot_cardinality": params.max_onehot_cardinality,
            "categorical_encodings": _json_serializable(encoding_mappings),
            "holdout_size": params.holdout_size,
            "split_seed": params.split_seed,
            "group_column": SUBJECT_KEY,
        }
        candidate_sets: list = []
        for n in top_features_list:
            n = min(n, X.shape[1])
            if params.method == "rfe":
                sel_feats, _ = _rfe_importance(n)
            else:
                sel_feats = importance_df["feature"].iloc[:n].tolist()
            candidate_sets.append((f"top_{n}", n, [str(name) for name in sel_feats]))
        candidate_sets.append(("all_features", None, [str(name) for name in X.columns]))

        frozen_sets: list = []
        for set_key, top_n, names in candidate_sets:
            set_frame = pd.DataFrame({SUBJECT_KEY: subject_keys})
            for name in names:
                # A measured column keeps its measured values — a missing value
                # stays missing, with its reason recorded. Encoded columns
                # exist only in X.
                source_column = df[name] if name in df.columns else X[name]
                set_frame[name] = source_column.values
            matrix, missing = matrix_from_frame(set_frame, names)
            set_config = {**base_config, "top_n": top_n}
            created = await domain_api.create_feature_set(
                set_key,
                {
                    "cohort_id": str(cohort["cohort_id"]),
                    "feature_names": names,
                    "feature_matrix": matrix,
                    "source_feature_ids": source_feature_ids,
                    "selection": selection,
                    "extraction_config": set_config,
                    "analysis": _label_free_qc(X, names, stability_scores),
                    "quality_summary": build_quality_summary(
                        subjects=len(matrix),
                        feature_count=len(names),
                        missing_values=missing,
                        notes=input_notes,
                    ),
                    "config_digest": provenance.document_digest(set_config),
                    "source_digest": source_digest,
                    "schema_version": FEATURE_SET_SCHEMA_VERSION,
                },
            )
            frozen = await domain_api.freeze_feature_set(str(created["feature_set_id"]))
            frozen_sets.append({
                "set_key": set_key,
                "feature_set_id": str(frozen["feature_set_id"]),
                "feature_count": len(names),
                "frozen_at": frozen.get("frozen_at"),
                "label_supervised_on_full_data": selection["label_supervised_on_full_data"],
            })

        # ADDED FOR UI ENHANCEMENT — Task 8: Sanitize chart_data before serialisation.
        # Recursively converts NaN/Infinity/numpy scalars to JSON-safe Python types.
        # React's JSON.parse crashes on NaN literals; this prevents that silently.
        chart_data_safe = _json_serializable(chart_data)

        payload = ProgressPayload(current=8, total=TOTAL_STAGES, label="Exporting top-N feature CSVs")
        log_tool_state(ToolEvent("tool_progress", "progress", payload))

        logger.info("Feature importance analysis complete.")
        payload = TextPayload(message="Feature Importance Analysis...Done")
        log_tool_state(ToolEvent("tool_finished", "text", payload))

        return json.dumps({
            "status": "success",
            "agent_run_id": run_id,
            "task_type": task_type,
            "method": params.method,
            "feature_count": int(X.shape[1]),
            "source": source_kind,
            # The frozen sets modeling reads by id.
            "feature_sets": frozen_sets,
            "exports": {
                f"feature_set_{item['set_key']}": (
                    f"/feature-sets/{item['feature_set_id']}/export"
                    "?format=csv&include_subject_key=true"
                )
                for item in frozen_sets
            },
            "figures": figures,
            "top_features": importance_df["feature"].head(min(10, len(importance_df))).tolist(),
            "warnings": selection_warnings,
            # Whether the selected feature set is blind to the holdout labels. When
            # True, every downstream holdout metric is optimistic by an unknown
            # amount and the report must say so rather than imply a clean estimate.
            "label_supervised_on_full_data": split.label_supervised_on_full_data,
            "selection_split": split_manifest,
            # ADDED FOR UI ENHANCEMENT - Task 8: chart_data sanitised via _json_serializable.
            # NaN/inf/numpy scalars are replaced with None so React never sees invalid JSON.
            "chart_data": chart_data_safe,
            # ADDED FOR UI ENHANCEMENT - Task 6: Alternative rankings (optional).
            # Keys absent when computation failed. UI must treat them as undefined.
            **(
                {
                    "alternative_rankings": _json_serializable(alternative_rankings),
                    "stability_scores": _json_serializable(stability_scores),
                }
                if (alternative_rankings or stability_scores)
                else {}
            ),
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
