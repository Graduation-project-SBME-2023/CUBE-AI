import nibabel as nib
import numpy as np
import pandas as pd
import os
from scipy.stats import skew, kurtosis, entropy
from skimage.feature import graycomatrix, graycoprops
from skimage.measure import regionprops, label
from scipy.ndimage import gaussian_laplace, binary_erosion
import pywt

# --------------------------
# Utility helpers
# --------------------------

def quantize_image(img, levels=64):
    """Quantize image intensities to `levels` discrete values (0..levels-1)."""
    img = img.astype(np.float32)
    mn, mx = np.nanmin(img), np.nanmax(img)
    if mx == mn:
        return np.zeros_like(img, dtype=np.uint8)
    q = ((img - mn) / (mx - mn) * (levels - 1)).clip(0, levels - 1)
    return q.astype(np.uint8)


def compute_voxel_spacing_mm(image_path):
    hdr = nib.load(image_path).header
    zooms = hdr.get_zooms()[:3]
    return np.array(zooms)

# --------------------------
# 3D shape features
# --------------------------

def compute_3d_shape_features(mask, voxel_spacing):
    """Compute volume (mm3), surface area (mm2), sphericity, compactness, elongation.
    mask: boolean numpy array in (Z,Y,X) or (D,H,W)
    voxel_spacing: iterable of (z_spacing, y_spacing, x_spacing)
    """
    props = {}
    voxel_volume = np.prod(voxel_spacing)
    vox_count = float(np.sum(mask))
    props['volume_mm3'] = float(vox_count * voxel_volume)

    # surface area approximation by counting exposed faces along each axis
    z, y, x = voxel_spacing
    # shift masks and find boundary faces
    mx = mask.astype(np.uint8)
    # faces perpendicular to x-axis have area = z * y
    face_x = np.abs(mx - np.pad(mx[:, :, :-1], ((0,0),(0,0),(1,0)), mode='constant'))
    face_y = np.abs(mx - np.pad(mx[:, :-1, :], ((0,0),(1,0),(0,0)), mode='constant'))
    face_z = np.abs(mx - np.pad(mx[:-1, :, :], ((1,0),(0,0),(0,0)), mode='constant'))

    area = (np.sum(face_x) * (z * y) +
            np.sum(face_y) * (z * x) +
            np.sum(face_z) * (y * x))
    props['surface_area_mm2'] = float(area)

    # sphericity = (pi^(1/3) * (6*V)^(2/3)) / A
    V = props['volume_mm3']
    A = props['surface_area_mm2']
    props['sphericity'] = float((np.pi ** (1.0/3.0)) * ((6.0 * V) ** (2.0/3.0)) / A) if A > 0 and V > 0 else np.nan

    # compactness (3D) = V^(2/3) / A
    props['compactness_3D'] = float((V ** (2.0/3.0)) / A) if A > 0 and V > 0 else np.nan

    # elongation: approximate from principal axes of inertia using moments
    coords = np.column_stack(np.nonzero(mask))
    if coords.shape[0] >= 3:
        cov = np.cov(coords.T)
        eigvals = np.linalg.eigvalsh(cov)
        eigvals = np.sort(np.maximum(eigvals, 1e-12))
        props['axis_ratio_1_3'] = float(np.sqrt(eigvals[-1] / eigvals[0]))
    else:
        props['axis_ratio_1_3'] = np.nan

    return props

# --------------------------
# Texture: GLCM (2D approximation averaged over slices)
# --------------------------

def glcm_features_3d(img, mask, distances=[1], angles=[0, np.pi/4, np.pi/2, 3*np.pi/4], levels=64):
    """Compute averaged GLCM properties across slices where the mask exists.
    We quantize image to `levels` and compute per-slice GLCM then average properties.
    """
    img_q = quantize_image(img, levels=levels)
    props = {"contrast": [], "homogeneity": [], "correlation": [], "ASM": [], "dissimilarity": []}

    # iterate over axial slices (assume z axis is first dim)
    for i in range(img_q.shape[0]):
        slice_mask = mask[i]
        if np.sum(slice_mask) < 10:
            continue
        slice_img = img_q[i]
        # crop to bbox to speed up
        ys, xs = np.where(slice_mask)
        y0, y1 = ys.min(), ys.max()
        x0, x1 = xs.min(), xs.max()
        crop_img = slice_img[y0:y1+1, x0:x1+1]
        crop_mask = slice_mask[y0:y1+1, x0:x1+1]
        if crop_img.size == 0:
            continue
        # set outside-mask to 0 so they don't contribute
        crop_img_masked = crop_img * crop_mask
        try:
            glcm = graycomatrix(crop_img_masked, distances=distances, angles=angles, levels=levels, symmetric=True, normed=True)
            props['contrast'].append(float(np.mean(graycoprops(glcm, "contrast"))))
            props['homogeneity'].append(float(np.mean(graycoprops(glcm, "homogeneity"))))
            props['correlation'].append(float(np.mean(graycoprops(glcm, "correlation"))))
            props['ASM'].append(float(np.mean(graycoprops(glcm, "ASM"))))
            props['dissimilarity'].append(float(np.mean(graycoprops(glcm, "dissimilarity"))))
        except Exception:
            # skip slices that fail
            continue

    # average over slices
    out = {}
    for k, v in props.items():
        out[k] = float(np.mean(v)) if len(v) > 0 else np.nan
    return out

# --------------------------
# GLRLM (3D approximate)
# --------------------------

def glrlm_features_3d(img, mask, levels=64):
    """Compute a few GLRLM-based features across three principal directions (x,y,z).
    This is a simplified implementation designed for moderate-sized volumes.
    Returns short-run emphasis and long-run emphasis (averaged over directions).
    """
    img_q = quantize_image(img, levels=levels)
    def runs_in_line(line):
        runs = []
        prev = line[0]
        length = 1
        for v in line[1:]:
            if v == prev:
                length += 1
            else:
                runs.append((prev, length))
                prev = v
                length = 1
        runs.append((prev, length))
        return runs

    sre_list = []
    lre_list = []
    for axis in range(3):
        # iterate lines along this axis
        for idx in np.ndindex(*(img_q.shape[j] for j in range(3) if j != axis)):
            # build slicing tuple
            sl = [slice(None)] * 3
            insert = 0
            for j in range(3):
                if j == axis:
                    continue
                sl[j] = idx[insert]
                insert += 1
            line = img_q[tuple(sl)]
            # apply mask along the same line
            mask_line = mask[tuple(sl)]
            if np.sum(mask_line) == 0:
                continue
            # only consider values inside mask
            line_vals = line[mask_line.astype(bool)]
            if line_vals.size == 0:
                continue
            runs = runs_in_line(line_vals)
            run_lengths = np.array([r[1] for r in runs], dtype=float)
            if run_lengths.size == 0:
                continue
            sre = np.sum(1.0 / (run_lengths ** 2 + 1e-12)) / run_lengths.size
            lre = np.sum(run_lengths ** 2) / run_lengths.size
            sre_list.append(sre)
            lre_list.append(lre)
    return {
        'GLRLM_short_run_emphasis': float(np.mean(sre_list)) if len(sre_list) > 0 else np.nan,
        'GLRLM_long_run_emphasis': float(np.mean(lre_list)) if len(lre_list) > 0 else np.nan
    }

# --------------------------
# GLSZM (3D)
# --------------------------

def glszm_features_3d(img, mask, levels=64):
    """Compute simplified GLSZM: zone entropy and large zone emphasis.
    We label connected components per gray-level inside the mask and compute zone sizes.
    """
    img_q = quantize_image(img, levels=levels)
    zone_sizes = []
    for g in range(levels):
        lvl_mask = (img_q == g) & mask
        if np.sum(lvl_mask) == 0:
            continue
        labeled = label(lvl_mask)
        props = np.bincount(labeled.ravel())
        # ignore background (index 0)
        if props.size > 1:
            sizes = props[1:]
            zone_sizes.extend(sizes.tolist())
    zone_sizes = np.array(zone_sizes, dtype=float)
    if zone_sizes.size == 0:
        return {
            'GLSZM_zone_entropy': np.nan,
            'GLSZM_large_zone_emphasis': np.nan
        }
    p = zone_sizes / np.sum(zone_sizes)
    zone_entropy = -np.sum(p * np.log2(p + 1e-12))
    lze = np.sum((zone_sizes ** 2)) / np.sum(zone_sizes)
    return {
        'GLSZM_zone_entropy': float(zone_entropy),
        'GLSZM_large_zone_emphasis': float(lze)
    }

# --------------------------
# GLDM (3D simplified)
# --------------------------

def gldm_features_3d(img, mask, dependence_threshold=1, distance=1, levels=64):
    """Simplified GLDM dependence variance: for each voxel, count number of neighbors within threshold.
    Then compute variance of those dependence counts (a proxy for GLDM dependence variance).
    """
    img_q = quantize_image(img, levels=levels)
    padded = np.pad(img_q, pad_width=distance, mode='constant', constant_values=-1)
    padded_mask = np.pad(mask.astype(np.uint8), pad_width=distance, mode='constant', constant_values=0)
    deps = []
    dz = [-1, 0, 1]
    for idx in zip(*np.nonzero(mask)):
        z, y, x = idx
        center = int(img_q[z, y, x])
        # define neighborhood box
        zs = slice(z - distance + distance, z + distance + 1 + distance)
        ys = slice(y - distance + distance, y + distance + 1 + distance)
        xs = slice(x - distance + distance, x + distance + 1 + distance)
        neigh = padded[zs, ys, xs]
        neigh_mask = padded_mask[zs, ys, xs]
        # count neighbors with intensity difference <= dependence_threshold
        valid = neigh_mask.astype(bool)
        if np.sum(valid) == 0:
            deps.append(0)
            continue
        diff = np.abs(neigh.astype(int) - center)
        cnt = int(np.sum((diff <= dependence_threshold) & valid)) - 1  # exclude center
        deps.append(cnt)
    deps = np.array(deps, dtype=float)
    if deps.size == 0:
        return {'GLDM_dependence_variance': np.nan}
    return {'GLDM_dependence_variance': float(np.var(deps))}

# --------------------------
# Main feature extraction
# --------------------------

def extract_radiomics_features(image_path, mask_path):
    img = nib.load(image_path).get_fdata()
    mask = nib.load(mask_path).get_fdata() > 0

    values = img[mask]
    if values.size == 0:
        return {key: np.nan for key in [
            "mean", "std", "skewness", "kurtosis", "entropy", "min", "max", "median"
        ]}

    # histogram for entropy/uniformity
    hist, _ = np.histogram(values, bins=32)
    hist = hist + 1e-12  # avoid log(0)
    hist = hist / hist.sum()

    features = {
        # --------------------------
        # First-order intensity features
        # --------------------------
        "mean": float(np.mean(values)),
        "std": float(np.std(values)),
        "skewness": float(skew(values)),
        "kurtosis": float(kurtosis(values)),
        "entropy": float(entropy(hist)),
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "median": float(np.median(values)),
        "energy": float(np.sum(values ** 2)),
        "uniformity": float(np.sum(hist ** 2)),
        "percentile_10": float(np.percentile(values, 10)),
        "percentile_90": float(np.percentile(values, 90)),
        "range": float(np.max(values) - np.min(values)),
        "mad": float(np.mean(np.abs(values - np.mean(values))))
    }

    # --------------------------
    # Shape-based features (3D)
    # --------------------------
    voxel_spacing = compute_voxel_spacing_mm(image_path)
    shape_feats = compute_3d_shape_features(mask, voxel_spacing)
    features.update(shape_feats)

    # --------------------------
    # Texture features (GLCM) - averaged over slices
    # --------------------------
    try:
        glcm_feats = glcm_features_3d(img, mask, distances=[1], angles=[0, np.pi/4, np.pi/2, 3*np.pi/4], levels=64)
        features.update(glcm_feats)
    except Exception:
        features.update({k: np.nan for k in ["contrast", "homogeneity", "correlation", "ASM", "dissimilarity"]})

    # --------------------------
    # Higher-order texture features (GLRLM, GLSZM, GLDM)
    # --------------------------
    try:
        features.update(glrlm_features_3d(img, mask, levels=64))
    except Exception:
        features.update({"GLRLM_short_run_emphasis": np.nan, "GLRLM_long_run_emphasis": np.nan})

    try:
        features.update(glszm_features_3d(img, mask, levels=64))
    except Exception:
        features.update({"GLSZM_zone_entropy": np.nan, "GLSZM_large_zone_emphasis": np.nan})

    try:
        features.update(gldm_features_3d(img, mask, dependence_threshold=1, distance=1, levels=64))
    except Exception:
        features.update({"GLDM_dependence_variance": np.nan})

    # --------------------------
    # Filter-based features (LoG / Wavelet)
    # --------------------------
    for sigma in [1.0, 2.0, 3.0]:
        filtered = gaussian_laplace(img, sigma=sigma)
        if np.sum(mask) > 0:
            features[f"LoG_mean_sigma_{sigma}"] = float(np.mean(filtered[mask]))
            features[f"LoG_std_sigma_{sigma}"] = float(np.std(filtered[mask]))
        else:
            features[f"LoG_mean_sigma_{sigma}"] = np.nan
            features[f"LoG_std_sigma_{sigma}"] = np.nan

    # Wavelet decomposition (n-D) and statistics on detail coefficients
    try:
        coeffs = pywt.wavedecn(img, wavelet='db1', level=1)
        # coeffs[0] is approximation, coeffs[1] is dict of details
        approx = coeffs[0]
        details = coeffs[1]
        # stats on approximation
        a_vals = approx[mask] if approx.shape == img.shape else approx
        if isinstance(a_vals, np.ndarray) and a_vals.size > 0:
            features['wavelet_approx_mean'] = float(np.mean(a_vals))
            features['wavelet_approx_std'] = float(np.std(a_vals))
        else:
            features['wavelet_approx_mean'] = np.nan
            features['wavelet_approx_std'] = np.nan
        # stats on detail bands
        if isinstance(details, dict):
            for k, band in details.items():
                band_vals = band[mask] if band.shape == img.shape else band
                features[f'wavelet_detail_{k}_mean'] = float(np.mean(band_vals)) if isinstance(band_vals, np.ndarray) and band_vals.size > 0 else np.nan
                features[f'wavelet_detail_{k}_std'] = float(np.std(band_vals)) if isinstance(band_vals, np.ndarray) and band_vals.size > 0 else np.nan
    except Exception:
        # fill with nans if wavelet fails
        features.update({
            'wavelet_approx_mean': np.nan,
            'wavelet_approx_std': np.nan
        })

    # --------------------------
    # Delta-radiomics: placeholder (computed later in analyze_all if multiple timepoints)
    # --------------------------
    features['delta_entropy'] = np.nan

    return features

# --------------------------
# Analyze all scans; handle multiple timepoints per case and compute delta features
# --------------------------

import json
from datetime import datetime

def analyze_all(images_dir, masks_dir, results_dir):
    data = []

    # Create a simple log file
    log_path = os.path.join(results_dir, "process_log.txt")
    with open(log_path, "a") as logf:
        logf.write(f"\n--- Radiomics analysis started at {datetime.now()} ---\n")

    # collect image files and group by case number
    image_files = [f for f in os.listdir(images_dir) if f.endswith('.nii.gz')]
    cases = {}
    for f in image_files:
        base = f.split('.nii.gz')[0]
        parts = base.split('_')
        nums = [p for p in parts if p.isdigit()]
        if len(nums) == 0:
            case = parts[1] if len(parts) > 1 else base
        else:
            case = nums[0]
        cases.setdefault(case, []).append(f)

    for case_num, files in cases.items():
        try:
            files_sorted = sorted(files)
            possible_labels = [
                f for f in os.listdir(masks_dir)
                if f.endswith('.nii.gz') and (f'_{case_num}.' in f or f'_{case_num}_' in f or f'case_{case_num}' in f)
            ]
            if len(possible_labels) == 0:
                print(f"[WARN] Missing label for case {case_num}, skipping.")
                with open(log_path, "a") as logf:
                    logf.write(f"{datetime.now()}: Missing label for case {case_num}, skipped.\n")
                continue

            label_file = possible_labels[0]
            mask_path = os.path.join(masks_dir, label_file)
            feats_tps = []

            for img_file in files_sorted:
                image_path = os.path.join(images_dir, img_file)
                feats = extract_radiomics_features(image_path, mask_path)
                feats['case'] = case_num
                feats['image_file'] = img_file
                feats_tps.append(feats)

            # compute deltas if multiple timepoints
            if len(feats_tps) >= 2:
                first = feats_tps[0].copy()
                last = feats_tps[-1].copy()
                delta = {
                    'case': case_num,
                    'image_file_first': feats_tps[0]['image_file'],
                    'image_file_last': feats_tps[-1]['image_file']
                }
                for k in first.keys():
                    if k in ['case', 'image_file']:
                        continue
                    v1 = first.get(k)
                    v2 = last.get(k)
                    try:
                        delta[f'delta_{k}'] = float(v2 - v1) if (
                            v1 is not None and v2 is not None and not np.isnan(v1) and not np.isnan(v2)
                        ) else np.nan
                    except Exception:
                        delta[f'delta_{k}'] = np.nan
                feats_tps.append(delta)

            # Save features for this case as JSON
            case_out_path = os.path.join(results_dir, f"case_{case_num}_features.json")
            with open(case_out_path, "w") as f:
                json.dump(feats_tps, f, indent=2)

            # Log success
            print(f"[INFO] Case {case_num} processed successfully. Saved to {case_out_path}")
            with open(log_path, "a") as logf:
                logf.write(f"{datetime.now()}: Case {case_num} processed successfully.\n")

            data.extend(feats_tps)

        except Exception as e:
            print(f"[ERROR] Failed to process case {case_num}: {e}")
            with open(log_path, "a") as logf:
                logf.write(f"{datetime.now()}: ERROR in case {case_num}: {str(e)}\n")

    with open(log_path, "a") as logf:
        logf.write(f"--- Radiomics analysis finished at {datetime.now()} ---\n")

    return pd.DataFrame(data)


def main():
    images_dir = r"D:\SBME\GP\Neuroblastoma\neuroblastoma_segmentation\data\nnUNet_raw\Dataset001_Neuroblastoma\imagesTr"
    masks_dir = r"D:\SBME\GP\Neuroblastoma\neuroblastoma_segmentation\data\nnUNet_raw\Dataset001_Neuroblastoma\labelsTr"
    results_dir = "./results/radiomics_analysis"
    os.makedirs(results_dir, exist_ok=True)

    df = analyze_all(images_dir, masks_dir, results_dir)

    out_path = os.path.join(results_dir, "radiomics_features_summary.csv")
    df.to_csv(out_path, index=False)

    print(f"\n✅ Radiomics analysis complete.\nSummary saved to: {out_path}")
    print(f"Detailed per-case JSONs and logs in: {results_dir}")

if __name__ == "__main__":
    main()
