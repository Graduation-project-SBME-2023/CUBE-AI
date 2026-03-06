"""
Radiomics & Feature Analysis MCP Tools

MCP-compatible wrappers for PyRadiomics feature extraction and feature importance analysis.
Follows the same structure as the scratchpad, classification, and regression MCP tools.

Tools exposed:
  - radiomics_extract_features     : Extract radiomic features from medical images + masks
  - radiomics_analyze_importance   : Feature importance analysis / selection on tabular data

──────────────────────────────────────────────────────────────────────────────
NOTE FOR AGENTS — how to pass list-valued parameters
──────────────────────────────────────────────────────────────────────────────
Several parameters that were previously typed as List[str] or List[int] are
now accepted as plain comma-separated strings so that any LLM or MCP client
can pass them without needing to serialise a JSON array.

  image_types        →  "Original,Wavelet,LoG"
  feature_classes    →  "firstorder,shape,glcm,glrlm"
  specific_features  →  "original_firstorder_Mean,original_shape_Sphericity"
  mask_labels        →  "1,2,3"            (integers after splitting)
  pixel_spacing      →  "1.0,1.0,1.0"      (floats after splitting)
  top_features       →  "10,50,100"         (integers after splitting)

Leave a parameter empty / omit it to use its documented default.
──────────────────────────────────────────────────────────────────────────────
"""

import json
import logging
import os
import re
import shutil
import tempfile
import time
from typing import Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from .. import mcp

# ── Constants ──────────────────────────────────────────────────────────────────

WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "../../../agent_system/workspace")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _setup_logging(log_file: str, verbose: bool = True) -> None:
    logger = logging.getLogger()
    logger.setLevel(logging.INFO if verbose else logging.WARNING)
    for h in logger.handlers[:]:
        logger.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    fh = logging.FileHandler(log_file)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)


def _csv_str(value: Optional[str]) -> Optional[list]:
    """Split a comma-separated string into a list of stripped strings, or None."""
    if value and value.strip():
        return [v.strip() for v in value.split(",") if v.strip()]
    return None


def _csv_int(value: Optional[str]) -> Optional[list]:
    """Split a comma-separated string into a list of ints, or None."""
    parts = _csv_str(value)
    if parts is None:
        return None
    try:
        return [int(p) for p in parts]
    except ValueError as exc:
        raise ValueError(f"Expected comma-separated integers, got: {value!r}") from exc


def _csv_float(value: Optional[str]) -> Optional[list]:
    """Split a comma-separated string into a list of floats, or None."""
    parts = _csv_str(value)
    if parts is None:
        return None
    try:
        return [float(p) for p in parts]
    except ValueError as exc:
        raise ValueError(f"Expected comma-separated floats, got: {value!r}") from exc


# ── Input Models ───────────────────────────────────────────────────────────────

class ExtractFeaturesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    # ── List-valued params passed as comma-separated strings ────────────────
    image_types: Optional[str] = Field(
        default=None,
        description=(
            "Comma-separated image pre-processing types to apply. "
            "Example: 'Original,Wavelet,LoG'  "
            "Available values: Original, Wavelet, LoG, Exponential, Gradient, SquareRoot, LBP2D, LBP3D. "
            "Defaults to 'Original,Wavelet' when omitted."
        ),
    )
    feature_classes: Optional[str] = Field(
        default=None,
        description=(
            "Comma-separated PyRadiomics feature classes to extract. "
            "Example: 'firstorder,shape,glcm'  "
            "Available: firstorder, shape, glcm, glrlm, glszm, gldm, ngtdm. "
            "Defaults to all classes when omitted (and specific_features is also omitted)."
        ),
    )
    specific_features: Optional[str] = Field(
        default=None,
        description=(
            "Comma-separated fully-qualified feature names to extract. "
            "Example: 'original_firstorder_Mean,original_shape_Sphericity,wavelet-LH_glcm_Contrast'  "
            "When provided, only these features are extracted and feature_classes is ignored. "
            "Omit to extract all features in the selected feature_classes."
        ),
    )
    mask_labels: Optional[str] = Field(
        default=None,
        description=(
            "Comma-separated integer label values to extract features for. "
            "Example: '1,2,3'  "
            "Omit to auto-detect all non-zero labels found in the masks."
        ),
    )
    pixel_spacing: Optional[str] = Field(
        default=None,
        description=(
            "Target voxel spacing for resampling as comma-separated floats [x,y,z]. "
            "Example: '1.0,1.0,1.0'  "
            "Only used when resample=true. Defaults to '1.0,1.0,1.0'."
        ),
    )

    # ── Scalar params ───────────────────────────────────────────────────────
    image_pattern: str = Field(
        default="*.nii.gz",
        description="Glob pattern to match image files. Example: '*.nii.gz' or '*.nii'.",
    )
    mask_pattern: str = Field(
        default="*.nii.gz",
        description="Glob pattern to match mask files.",
    )
    normalize: bool = Field(
        default=True, description="Normalize image intensity values before extraction."
    )
    bin_width: float = Field(
        default=25.0, description="Bin width for discretising image intensities."
    )
    resample: bool = Field(
        default=True, description="Resample images to isotropic voxels before extraction."
    )
    force_2d: bool = Field(
        default=False,
        description="Extract features slice-by-slice (2D) instead of volumetrically (3D).",
    )
    n_workers: int = Field(
        default=1,
        description="Number of parallel worker processes. Use 1 for serial (safer) processing.",
    )

    # ── Optional target linking ─────────────────────────────────────────────
    targets_csv: Optional[str] = Field(
        default=None,
        description="Path to a CSV file containing target/outcome values per subject.",
    )
    id_column: Optional[str] = Field(
        default=None,
        description="Column name for subject IDs in targets_csv. Defaults to the first column.",
    )
    target_column: Optional[str] = Field(
        default=None,
        description="Column name for target values in targets_csv. Defaults to the second column.",
    )
    id_pattern: Optional[str] = Field(
        default=None,
        description=(
            "Regex pattern with one capture group to extract a subject ID from filenames. "
            "Example: r'^(sub-\\d+)_' to capture 'sub-001' from 'sub-001_T1w.nii.gz'. "
            "Omit to use the full filename (minus extension) as the subject ID."
        ),
    )


class AnalyzeImportanceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    input_path: str = Field(
        ..., description="Path to the input CSV file (features + target column)."
    )
    # output_dir: str = Field(
    #     ..., description="Directory where importance CSVs, selected-feature CSVs, and plots are saved."
    # )
    # target_column: str = Field(
    #     ..., description="Name of the column to predict."
    # )

    # ── List-valued param passed as comma-separated string ──────────────────
    top_features: str = Field(
        default="10",
        description=(
            "Comma-separated list of feature-count thresholds to export. "
            "Example: '10,50,100'  "
            "For each value N, a separate CSV is saved containing only the top-N features. "
            "Values larger than the total number of available features are clamped automatically."
        ),
    )

    # ── Scalar params ───────────────────────────────────────────────────────
    task_type: Optional[str] = Field(
        default=None,
        description=(
            "Machine learning task: 'classification' or 'regression'. "
            "Auto-detected from the target column when omitted."
        ),
    )
    method: str = Field(
        default="rf",
        description=(
            "Feature selection algorithm. One of: "
            "'rf' (Random Forest importance, recommended), "
            "'f_test' (ANOVA F-value / F-regression), "
            "'mutual_info' (mutual information), "
            "'rfe' (Recursive Feature Elimination with RF estimator)."
        ),
    )
    encode_categorical: str = Field(
        default="auto",
        description=(
            "How to handle categorical (non-numeric) feature columns. "
            "'auto' — one-hot if ≤ max_onehot_cardinality unique values, else label encoding; "
            "'onehot' — always one-hot; "
            "'label' — always label encoding; "
            "'target' — target encoding + frequency encoding; "
            "'none' — drop categorical columns entirely."
        ),
    )
    max_onehot_cardinality: int = Field(
        default=10,
        description="Maximum unique values a categorical column may have before 'auto' switches to label encoding.",
    )
    create_plots: bool = Field(
        default=True,
        description="Generate feature importance bar chart, cumulative importance curve, correlation heatmap, PCA, and t-SNE plots.",
    )
    top_n_plot: int = Field(
        default=30,
        description="Number of top features to display in the importance bar chart.",
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
    """Extract radiomic features from medical images and segmentation masks using PyRadiomics.

    Workflow:
      1. Match image files to mask files by subject ID (derived from filename or id_pattern).
      2. Auto-detect non-zero label values in masks (or use mask_labels).
      3. For each subject × label: create a binary mask, run the PyRadiomics extractor,
         collect first-order, shape, texture (GLCM, GLRLM, GLSZM, GLDM, NGTDM) features.
      4. Save one CSV per label to output_dir (e.g. radiomic_features_label_1.csv).
      5. Optionally merge a targets column from targets_csv.

    AGENT NOTE — passing list parameters:
      All list-valued parameters are accepted as comma-separated strings.
      • image_types      →  "Original,Wavelet"
      • feature_classes  →  "firstorder,shape,glcm"
      • specific_features →  "original_firstorder_Mean,original_shape_Sphericity"
      • mask_labels      →  "1,2"
      • pixel_spacing    →  "1.0,1.0,1.0"

    Args:
        params (ExtractFeaturesInput): Extraction configuration – see field descriptions.

    Returns:
        str: JSON with keys:
            - status                  : "success" or "error"
            - csv_paths               : {label_N: path_to_csv, ...}
            - num_subjects            : number of matched image-mask pairs processed
            - num_features            : {label_N: feature_count, ...}
            - mask_labels             : list of label values that were processed
            - params_file             : path to the saved PyRadiomics YAML params
            - processing_time_seconds : wall-clock time
            - error_message           : present only when status == "error"
    """
    import glob as _glob
    import yaml

    try:
        import SimpleITK as sitk
        from radiomics import featureextractor
    except ImportError as exc:
        return json.dumps({
            "status": "error",
            "error_message": "PyRadiomics / SimpleITK not installed. Run: pip install pyradiomics simpleitk",
        }, indent=2)

    output_dir = os.path.join(WORKSPACE_DIR, 'features')
    image_dir = os.path.join(WORKSPACE_DIR, 'volumes')
    mask_dir = os.path.join(WORKSPACE_DIR, 'masks')
    target_column = 'target'

    os.makedirs(output_dir, exist_ok=True)
    log_file = os.path.join(output_dir, "radiomics_extraction.log")
    _setup_logging(log_file)
    logger = logging.getLogger()

    # Resolve comma-separated list params
    image_types   = _csv_str(params.image_types)   or ["Original", "Wavelet"]
    feature_classes = _csv_str(params.feature_classes)
    specific_features = _csv_str(params.specific_features)
    mask_labels_raw = _csv_int(params.mask_labels)   # None = auto-detect
    pixel_spacing  = _csv_float(params.pixel_spacing) or [1.0, 1.0, 1.0]

    temp_dir = None
    try:
        # ── Validate input dirs ─────────────────────────────────────────────
        for path, name in [(image_dir, "image_dir"), (mask_dir, "mask_dir")]:
            if not os.path.isdir(path):
                raise ValueError(f"{name} does not exist or is not a directory: {path}")

        # ── Build PyRadiomics params dict ───────────────────────────────────
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
        logger.info(f"PyRadiomics params saved → {params_file}")

        extractor = featureextractor.RadiomicsFeatureExtractor(params_file)
        logger.info("Extractor initialised.")

        # ── Load targets ────────────────────────────────────────────────────
        target_dict: dict = {}
        if params.targets_csv and os.path.exists(params.targets_csv):
            tdf = pd.read_csv(params.targets_csv)
            id_col  = params.id_column  or tdf.columns[0]
            tgt_col = target_column or tdf.columns[1]
            target_dict = {str(row[id_col]): row[tgt_col] for _, row in tdf.iterrows()}
            logger.info(f"Loaded {len(target_dict)} target values from {params.targets_csv}")

        # ── Find files ──────────────────────────────────────────────────────
        image_files = sorted(_glob.glob(os.path.join(image_dir, params.image_pattern)))
        mask_files  = sorted(_glob.glob(os.path.join(mask_dir,  params.mask_pattern)))

        if not image_files:
            raise ValueError(f"No images found in {image_dir} matching '{params.image_pattern}'")
        if not mask_files:
            raise ValueError(f"No masks found in {mask_dir} matching '{params.mask_pattern}'")

        logger.info(f"Found {len(image_files)} images and {len(mask_files)} masks")

        # ── Match images ↔ masks ────────────────────────────────────────────
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

        pairs = {
            sid: {"image": images_dict[sid], "mask": masks_dict[sid]}
            for sid in images_dict
            if sid in masks_dict
        }
        unmatched_images = [images_dict[s] for s in images_dict if s not in masks_dict]
        unmatched_masks  = [masks_dict[s]  for s in masks_dict  if s not in images_dict]
        if unmatched_images or unmatched_masks:
            logger.warning(f"Unmatched — images: {len(unmatched_images)}, masks: {len(unmatched_masks)}")

        if not pairs:
            raise ValueError("No matching image-mask pairs found. Check filenames and id_pattern.")

        logger.info(f"Matched {len(pairs)} pairs")

        # ── Auto-detect labels ──────────────────────────────────────────────
        if mask_labels_raw is None:
            found: set = set()
            for mf in list(masks_dict.values())[:10]:
                try:
                    arr = sitk.GetArrayFromImage(sitk.ReadImage(mf))
                    for lbl in np.unique(arr):
                        if lbl > 0:
                            found.add(int(lbl))
                except Exception:
                    pass
            mask_labels_raw = sorted(found) or [1]
            logger.info(f"Auto-detected labels: {mask_labels_raw}")

        # ── Extract features ────────────────────────────────────────────────
        all_features_by_label: dict = {lbl: [] for lbl in mask_labels_raw}
        temp_dir = tempfile.mkdtemp(dir=output_dir)
        start = time.time()

        for idx, (subject_id, files) in enumerate(pairs.items(), 1):
            logger.info(f"Processing {subject_id} ({idx}/{len(pairs)})")
            try:
                orig_image = sitk.ReadImage(files["image"])
                orig_mask  = sitk.ReadImage(files["mask"])
                mask_array = sitk.GetArrayFromImage(orig_mask)

                for lbl in mask_labels_raw:
                    binary = (mask_array == lbl).astype(np.uint8)
                    if binary.sum() == 0:
                        logger.info(f"  {subject_id} label {lbl}: no voxels, skipping")
                        continue

                    bin_img = sitk.GetImageFromArray(binary)
                    bin_img.CopyInformation(orig_mask)
                    tmp_mask_path = os.path.join(temp_dir, f"{subject_id}_lbl{lbl}.nii.gz")
                    sitk.WriteImage(bin_img, tmp_mask_path)

                    try:
                        result = extractor.execute(files["image"], tmp_mask_path)
                        features = {
                            str(k): v for k, v in result.items()
                            if not k.startswith("diagnostics_")
                        }
                        features["subject_id"] = subject_id
                        if subject_id in target_dict:
                            features["target"] = target_dict[subject_id]
                        all_features_by_label[lbl].append(features)
                        logger.info(f"  {subject_id} label {lbl}: {len(features)} features extracted")
                    except Exception as exc:
                        logger.error(f"  {subject_id} label {lbl}: extraction failed — {exc}")
                    finally:
                        if os.path.exists(tmp_mask_path):
                            os.remove(tmp_mask_path)

            except Exception as exc:
                logger.error(f"Error processing {subject_id}: {exc}")

        processing_time = time.time() - start
        logger.info(f"Extraction finished in {processing_time:.1f}s")

        # ── Save CSVs ───────────────────────────────────────────────────────
        csv_paths: dict = {}
        num_features: dict = {}

        for lbl, rows in all_features_by_label.items():
            if not rows:
                logger.warning(f"No features for label {lbl}")
                continue
            df = pd.DataFrame(rows)
            id_cols  = [c for c in df.columns if c.lower() in ("id", "subject_id", "patientid")]
            tgt_cols = [c for c in df.columns if c.lower() in ("target", "label", "outcome")]
            feat_cols = sorted([c for c in df.columns if c not in id_cols + tgt_cols])
            df = df[id_cols + tgt_cols + feat_cols]
            csv_path = os.path.join(output_dir, f"radiomic_features_label_{lbl}.csv")
            df.to_csv(csv_path, index=False)
            csv_paths[f"label_{lbl}"] = csv_path
            num_features[lbl] = len(feat_cols)
            logger.info(f"Saved label {lbl} → {csv_path} ({len(df)} subjects, {len(feat_cols)} features)")

        return json.dumps({
            "status": "success",
            "csv_paths": csv_paths,
            "num_subjects": len(pairs),
            "num_features": num_features,
            "mask_labels": mask_labels_raw,
            "params_file": params_file,
            "processing_time_seconds": round(processing_time, 2),
        }, indent=2)

    except Exception as exc:
        logger.error(f"Unhandled error: {exc}", exc_info=True)
        return json.dumps({
            "status": "error",
            "error_message": str(exc),
            "image_dir": image_dir,
            "mask_dir": mask_dir,
            "output_dir": output_dir,
        }, indent=2)

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
    """Perform feature importance analysis on a tabular CSV and export top-N feature subsets.

    Workflow:
      1. Load the CSV and auto-detect (or accept) the task type.
      2. Encode categorical columns if present.
      3. Compute feature importance scores using the selected method.
      4. For each count N in top_features, save a CSV with only the top-N features.
      5. Optionally generate: importance bar chart, cumulative importance curve,
         correlation heatmap, PCA plot, and t-SNE plot.

    AGENT NOTE — passing top_features:
      Pass a comma-separated string of integers.
      Example: top_features="10,50,100"
      A separate output CSV is produced for each value.

    Args:
        params (AnalyzeImportanceInput): Analysis configuration – see field descriptions.

    Returns:
        str: JSON with keys:
            - status         : "success" or "error"
            - output_dir     : str
            - task_type      : detected or supplied task type
            - method         : feature selection method used
            - feature_count  : total number of features in the input
            - result_files   : {"importance": path, "selected_features": {N: path, ...}}
            - error_message  : present only when status == "error"
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

    output_dir = os.path.join(WORKSPACE_DIR, 'features')

    os.makedirs(output_dir, exist_ok=True)
    log_file = os.path.join(output_dir, "feature_importance_analysis.log")
    _setup_logging(log_file)
    logger = logging.getLogger()

    target_column = 'target'

    try:
        # ── Validate ────────────────────────────────────────────────────────
        if not os.path.exists(params.input_path):
            raise ValueError(f"Input file not found: {params.input_path}")
        valid_methods = ("rf", "f_test", "mutual_info", "rfe")
        if params.method not in valid_methods:
            raise ValueError(f"method must be one of {valid_methods}, got '{params.method}'")
        valid_enc = ("auto", "onehot", "label", "target", "none")
        if params.encode_categorical not in valid_enc:
            raise ValueError(f"encode_categorical must be one of {valid_enc}")

        top_features_list = sorted(set(_csv_int(params.top_features) or [10]))

        # ── Load data ───────────────────────────────────────────────────────
        df = pd.read_csv(params.input_path)
        if target_column not in df.columns:
            raise ValueError(f"target_column {target_column} not found in data.")

        # Drop subject_id if present (never a feature)
        X = df.drop(columns=[target_column])
        if "subject_id" in X.columns:
            X = X.drop(columns=["subject_id"])
        y = df[target_column]

        # ── Auto-detect task type ───────────────────────────────────────────
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

        # ── Encode categoricals ─────────────────────────────────────────────
        cat_cols = X.select_dtypes(include=["object", "category"]).columns.tolist()
        encoding_mappings: dict = {}

        if params.encode_categorical != "none" and cat_cols:
            logger.info(f"Encoding {len(cat_cols)} categorical columns using '{params.encode_categorical}'")
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
                    encoding_mappings[col] = {
                        "method": "label",
                        "mapping": dict(zip(le.classes_, range(len(le.classes_)))),
                    }
                elif method == "target":
                    tmp = pd.concat([X[[col]], y], axis=1)
                    means = tmp.groupby(col)[target_column].mean()
                    X[f"{col}_target"] = X[col].map(means)
                    X[f"{col}_freq"]   = X[col].map(X[col].value_counts(normalize=True))
                    encoding_mappings[col] = {"method": "target"}

            X = X.drop(columns=cat_cols)
        elif params.encode_categorical == "none" and cat_cols:
            logger.warning(f"Dropping {len(cat_cols)} categorical columns (encode_categorical='none')")
            X = X.select_dtypes(include=[np.number])

        # Fill any remaining NaNs
        num_cols = X.select_dtypes(include=[np.number]).columns
        X[num_cols] = X[num_cols].fillna(X[num_cols].median())
        X = X.fillna(0)

        # Save encoding mappings
        if encoding_mappings:
            enc_path = os.path.join(output_dir, "categorical_encodings.json")
            with open(enc_path, "w") as fh:
                json.dump(encoding_mappings, fh, indent=2, default=str)
            logger.info(f"Encoding mappings saved → {enc_path}")

        logger.info(f"Feature matrix: {X.shape[0]} rows × {X.shape[1]} features")

        # ── Feature importance helpers ──────────────────────────────────────
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
            return X.columns[sel.get_support()].tolist(), sel.ranking_

        method_fn = {
            "rf": _rf_importance,
            "f_test": _f_test_importance,
            "mutual_info": _mutual_info_importance,
            "rfe": _rfe_importance,
        }[params.method]

        # ── Compute global importance (all features) ────────────────────────
        all_feats, scores = _rf_importance(X.shape[1]) if params.method == "rfe" else method_fn(X.shape[1])
        importance_df = pd.DataFrame({"feature": X.columns, "importance": scores})
        importance_df = importance_df.sort_values("importance", ascending=False).reset_index(drop=True)

        importance_path = os.path.join(output_dir, "feature_importance.csv")
        importance_df.to_csv(importance_path, index=False)
        logger.info(f"Importance scores saved → {importance_path}")

        # ── Generate plots ──────────────────────────────────────────────────
        plots_dir = os.path.join(output_dir, "plots")
        os.makedirs(plots_dir, exist_ok=True)

        if params.create_plots:
            # Bar chart
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

            # Cumulative importance
            try:
                plt.figure(figsize=(10, 6))
                cum = importance_df["importance"].cumsum() / importance_df["importance"].sum()
                plt.plot(range(1, len(cum) + 1), cum)
                for thr, color in zip([0.8, 0.9, 0.95, 0.99], ["r", "g", "orange", "purple"]):
                    n = int((cum >= thr).argmax()) + 1
                    plt.axhline(thr, color=color, linestyle="--", label=f"{thr*100:.0f}%: {n} features")
                plt.xlabel("Number of Features"); plt.ylabel("Cumulative Importance")
                plt.title("Cumulative Feature Importance"); plt.legend(loc="lower right")
                plt.tight_layout()
                plt.savefig(os.path.join(plots_dir, "cumulative_importance_plot.png"), dpi=300, bbox_inches="tight")
                plt.close()
            except Exception as exc:
                logger.warning(f"Cumulative plot failed: {exc}"); plt.close()

            # Correlation heatmap (top 20)
            try:
                top20 = importance_df["feature"].iloc[:20].tolist()
                top20 = [f for f in top20 if f in X.columns]
                if len(top20) >= 2:
                    corr = X[top20].corr()
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

        # ── Export top-N feature CSVs ───────────────────────────────────────
        selected_files: dict = {}

        for n in top_features_list:
            n = min(n, X.shape[1])

            if params.method == "rfe":
                sel_feats, _ = _rfe_importance(n)
            else:
                sel_feats = importance_df["feature"].iloc[:n].tolist()

            # Build output dataframe — include original (pre-encoded) columns where possible
            cols_to_save = [c for c in sel_feats if c in df.columns]
            encoded_only = [c for c in sel_feats if c not in df.columns]

            out_df = df[cols_to_save + [target_column]].copy() if cols_to_save else pd.DataFrame({target_column: df[target_column]})
            for col in encoded_only:
                if col in X.columns:
                    out_df[col] = X[col].values

            out_path = os.path.join(output_dir, f"top_{n}_features.csv")
            out_df.to_csv(out_path, index=False)
            selected_files[str(n)] = out_path
            logger.info(f"Top-{n} features saved → {out_path}")

            # PCA + t-SNE per subset
            if params.create_plots and n > 1:
                valid = [f for f in sel_feats if f in X.columns]
                if len(valid) >= 2:
                    X_sub = X[valid]
                    # PCA
                    try:
                        pca = PCA(n_components=2, random_state=42)
                        Xp = pca.fit_transform(X_sub)
                        pca_df = pd.DataFrame({"PC1": Xp[:, 0], "PC2": Xp[:, 1], "target": y})
                        plt.figure(figsize=(8, 6))
                        if task_type == "classification":
                            sns.scatterplot(x="PC1", y="PC2", hue="target", data=pca_df, palette="viridis", alpha=0.7)
                        else:
                            sc = plt.scatter(pca_df["PC1"], pca_df["PC2"], c=pca_df["target"], cmap="viridis", alpha=0.6)
                            plt.colorbar(sc)
                        ev = pca.explained_variance_ratio_
                        plt.title(f"PCA – top {n} features (var={sum(ev):.1%})")
                        plt.tight_layout()
                        plt.savefig(os.path.join(plots_dir, f"pca_top_{n}.png"), dpi=200, bbox_inches="tight")
                        plt.close()
                    except Exception as exc:
                        logger.warning(f"PCA plot failed (n={n}): {exc}"); plt.close()

                    # t-SNE (only for reasonably-sized datasets)
                    if len(X_sub) <= 5000 or n <= 100:
                        try:
                            perp = min(30, len(X_sub) - 1)
                            tsne = TSNE(n_components=2, perplexity=perp, random_state=42, n_jobs=-1)
                            Xt = tsne.fit_transform(X_sub)
                            tsne_df = pd.DataFrame({"D1": Xt[:, 0], "D2": Xt[:, 1], "target": y})
                            plt.figure(figsize=(8, 6))
                            if task_type == "classification":
                                sns.scatterplot(x="D1", y="D2", hue="target", data=tsne_df, palette="viridis", alpha=0.7)
                            else:
                                sc = plt.scatter(tsne_df["D1"], tsne_df["D2"], c=tsne_df["target"], cmap="viridis", alpha=0.6)
                                plt.colorbar(sc)
                            plt.title(f"t-SNE – top {n} features")
                            plt.tight_layout()
                            plt.savefig(os.path.join(plots_dir, f"tsne_top_{n}.png"), dpi=200, bbox_inches="tight")
                            plt.close()
                        except Exception as exc:
                            logger.warning(f"t-SNE plot failed (n={n}): {exc}"); plt.close()

        logger.info("Feature importance analysis complete.")
        return json.dumps({
            "status": "success",
            "output_dir": output_dir,
            "task_type": task_type,
            "method": params.method,
            "feature_count": int(X.shape[1]),
            "result_files": {
                "importance": importance_path,
                "selected_features": selected_files,
            },
        }, indent=2)

    except Exception as exc:
        logging.error(f"Unhandled error in radiomics_analyze_importance: {exc}", exc_info=True)
        return json.dumps({
            "status": "error",
            "error_message": str(exc),
            "input_path": params.input_path,
            "output_dir": output_dir,
        }, indent=2)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
