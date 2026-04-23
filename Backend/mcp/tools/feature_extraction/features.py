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
from typing import Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from .. import mcp
from ..helpers.workspace import WorkspacePath, base_on_workspace_dir


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
    if value and value.strip():
        return [v.strip() for v in value.split(",") if v.strip()]
    return None


def _csv_int(value: Optional[str]) -> Optional[list]:
    parts = _csv_str(value)
    if parts is None:
        return None
    try:
        return [int(p) for p in parts]
    except ValueError as exc:
        raise ValueError(f"Expected comma-separated integers, got: {value!r}") from exc


def _csv_float(value: Optional[str]) -> Optional[list]:
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
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    image_types: Optional[str] = Field(default=None)
    feature_classes: Optional[str] = Field(default=None)
    specific_features: Optional[str] = Field(default=None)
    mask_labels: Optional[str] = Field(default=None)
    pixel_spacing: Optional[str] = Field(default=None)
    image_pattern: str = Field(default="*.nii.gz")
    mask_pattern: str = Field(default="*.nii.gz")
    normalize: bool = Field(default=True)
    bin_width: float = Field(default=25.0)
    resample: bool = Field(default=True)
    force_2d: bool = Field(default=False)
    n_workers: int = Field(default=1)
    targets_csv: WorkspacePath = Field(default=None)
    id_column: Optional[str] = Field(default=None)
    target_column: Optional[str] = Field(default=None)
    id_pattern: Optional[str] = Field(default=None)


class AnalyzeImportanceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    input_path: WorkspacePath = Field(...)
    top_features: str = Field(default="10")
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
    (Unchanged from original — no chart_data needed here, FeaturesView gets it from analyze_importance.)
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

    output_dir = base_on_workspace_dir('features')
    image_dir = base_on_workspace_dir('volumes')
    mask_dir = base_on_workspace_dir('masks')
    target_column = 'target'

    os.makedirs(output_dir, exist_ok=True)
    log_file = os.path.join(output_dir, "radiomics_extraction.log")
    _setup_logging(log_file)
    logger = logging.getLogger()

    image_types   = _csv_str(params.image_types)   or ["Original", "Wavelet"]
    feature_classes = _csv_str(params.feature_classes)
    specific_features = _csv_str(params.specific_features)
    mask_labels_raw = _csv_int(params.mask_labels)
    pixel_spacing  = _csv_float(params.pixel_spacing) or [1.0, 1.0, 1.0]

    temp_dir = None
    try:
        for path, name in [(image_dir, "image_dir"), (mask_dir, "mask_dir")]:
            if not os.path.isdir(path):
                raise ValueError(f"{name} does not exist or is not a directory: {path}")

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
        if params.targets_csv and os.path.exists(params.targets_csv):
            tdf = pd.read_csv(params.targets_csv)
            id_col = params.id_column or tdf.columns[0]
            tgt_col = target_column or tdf.columns[1]
            target_dict = {str(row[id_col]): row[tgt_col] for _, row in tdf.iterrows()}

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
        pairs = {
            sid: {"image": images_dict[sid], "mask": masks_dict[sid]}
            for sid in images_dict if sid in masks_dict
        }

        if not pairs:
            raise ValueError("No matching image-mask pairs found.")

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
                        continue
                    bin_img = sitk.GetImageFromArray(binary)
                    bin_img.CopyInformation(orig_mask)
                    tmp_mask_path = os.path.join(temp_dir, f"{subject_id}_lbl{lbl}.nii.gz")
                    sitk.WriteImage(bin_img, tmp_mask_path)
                    try:
                        result = extractor.execute(files["image"], tmp_mask_path)
                        features = {str(k): v for k, v in result.items() if not k.startswith("diagnostics_")}
                        features["subject_id"] = subject_id
                        if subject_id in target_dict:
                            features["target"] = target_dict[subject_id]
                        all_features_by_label[lbl].append(features)
                    except Exception as exc:
                        logger.error(f"  {subject_id} label {lbl}: extraction failed — {exc}")
                    finally:
                        if os.path.exists(tmp_mask_path):
                            os.remove(tmp_mask_path)
            except Exception as exc:
                logger.error(f"Error processing {subject_id}: {exc}")

        processing_time = time.time() - start

        csv_paths: dict = {}
        num_features: dict = {}

        for lbl, rows in all_features_by_label.items():
            if not rows:
                continue
            df = pd.DataFrame(rows)
            id_cols = [c for c in df.columns if c.lower() in ("id", "subject_id", "patientid")]
            tgt_cols = [c for c in df.columns if c.lower() in ("target", "label", "outcome")]
            feat_cols = sorted([c for c in df.columns if c not in id_cols + tgt_cols])
            df = df[id_cols + tgt_cols + feat_cols]
            csv_path = os.path.join(output_dir, f"radiomic_features_label_{lbl}.csv")
            df.to_csv(csv_path, index=False)
            csv_paths[f"label_{lbl}"] = csv_path
            num_features[lbl] = len(feat_cols)

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
    logger = logging.getLogger()

    target_column = 'target'

    try:
        if not os.path.exists(params.input_path):
            raise ValueError(f"Input file not found: {params.input_path}")
        valid_methods = ("rf", "f_test", "mutual_info", "rfe")
        if params.method not in valid_methods:
            raise ValueError(f"method must be one of {valid_methods}")
        valid_enc = ("auto", "onehot", "label", "target", "none")
        if params.encode_categorical not in valid_enc:
            raise ValueError(f"encode_categorical must be one of {valid_enc}")

        top_features_list = sorted(set(_csv_int(params.top_features) or [10]))

        df = pd.read_csv(params.input_path)
        if target_column not in df.columns:
            raise ValueError(f"target_column '{target_column}' not found in data.")

        X = df.drop(columns=[target_column])
        if "subject_id" in X.columns:
            X = X.drop(columns=["subject_id"])
        y = df[target_column]

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

        # ── Importance computation ──────────────────────────────────────────
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

        method_fn = {"rf": _rf_importance, "f_test": _f_test_importance,
                     "mutual_info": _mutual_info_importance, "rfe": _rfe_importance}[params.method]

        all_feats, scores = method_fn(X.shape[1])
        importance_df = pd.DataFrame({"feature": X.columns, "importance": scores})
        importance_df = importance_df.sort_values("importance", ascending=False).reset_index(drop=True)

        importance_path = os.path.join(output_dir, "feature_importance.csv")
        importance_df.to_csv(importance_path, index=False)

        # ── Build chart_data ────────────────────────────────────────────────
        chart_data: dict = {"task_type": task_type, "method": params.method}

        # 1. Importance bars (top top_n_plot)
        top_plot = importance_df.head(min(params.top_n_plot, len(importance_df)))
        chart_data["importance_bars"] = [
            {"feature": str(row["feature"]), "importance": _safe(row["importance"])}
            for _, row in top_plot.iterrows()
        ]

        # 2. Cumulative importance curve (full, down-sampled to ≤300 pts)
        total_imp = importance_df["importance"].sum()
        cum = importance_df["importance"].cumsum()
        cum_pct = (cum / total_imp * 100) if total_imp > 0 else cum * 0
        step = max(1, len(cum_pct) // 300)
        chart_data["cumulative"] = [
            {"n_features": int(i + 1), "cumulative_pct": round(float(v), 3)}
            for i, v in enumerate(cum_pct)
            if i % step == 0 or i == len(cum_pct) - 1
        ]

        # 3. Correlation matrix (top 20 features)
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

        # 4 & 5. PCA + t-SNE — use top-N subset (first value in top_features_list)
        top_n_scatter = min(top_features_list[-1], X.shape[1])
        scatter_feats = [f for f in importance_df["feature"].iloc[:top_n_scatter].tolist() if f in X.columns]
        chart_data["pca"] = None
        chart_data["tsne"] = None

        if len(scatter_feats) >= 2:
            X_sub = X[scatter_feats]

            # PCA
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

            # t-SNE (only for ≤5000 samples to keep time reasonable)
            if len(X_sub) <= 5000:
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

        # ── Also still save plots for archival purposes ─────────────────────
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

        # ── Export top-N feature CSVs ───────────────────────────────────────
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