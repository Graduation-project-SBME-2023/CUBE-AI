"""
Frontend View Test Harness
=========================
Emits realistic dummy payloads via WebSocket to test ModelingView and FeaturesView
without running any agents or tools.

Usage:
    python test_views.py [--host HOST] [--port PORT] [--session SESSION_ID] [--views all|modeling|features]

Examples:
    python test_views.py
    python test_views.py --views modeling
    python test_views.py --views features
    python test_views.py --host localhost --port 8150 --session my-test-session
"""

import argparse
import asyncio
import json
import math
import random
import sys
import httpx

# ── Config ─────────────────────────────────────────────────────────────────────

DEFAULT_HOST    = "localhost"
DEFAULT_PORT    = 8150
DEFAULT_SESSION = "test-session-001"

# ── RNG seed for reproducibility ───────────────────────────────────────────────

random.seed(42)

# ── Curve generators ───────────────────────────────────────────────────────────

def _roc_curve(auc: float, n_pts: int = 80):
    """Generate a plausible ROC curve for a given AUC."""
    # Parametric curve: tpr = fpr^(1 / (2*auc - 1)) for auc > 0.5
    fpr = [i / (n_pts - 1) for i in range(n_pts)]
    exponent = max(0.05, 1 / max(0.01, 2 * auc - 1))
    tpr = [round(f ** exponent + random.gauss(0, 0.005), 5) for f in fpr]
    tpr = [max(0.0, min(1.0, v)) for v in tpr]
    tpr[0], tpr[-1] = 0.0, 1.0
    return {"fpr": fpr, "tpr": tpr}


def _pr_curve(auc: float, n_pts: int = 80):
    """Generate a plausible PR curve."""
    recall = [i / (n_pts - 1) for i in range(n_pts)]
    # High-AUC models maintain precision longer
    base = auc
    precision = [round(base * (1 - r ** (1.5 * (1 - auc + 0.3))) + random.gauss(0, 0.01), 5) for r in recall]
    precision = [max(0.0, min(1.0, v)) for v in precision]
    precision[0] = 1.0
    return {"precision": precision, "recall": recall}


def _cumulative(n_features: int = 200):
    """Generate cumulative importance curve data."""
    # Exponential decay in importance (Pareto-like)
    importances = [1.0 / (i + 1) ** 0.8 for i in range(n_features)]
    total = sum(importances)
    cumsum = 0.0
    pts = []
    for i, imp in enumerate(importances):
        cumsum += imp
        pts.append({"n_features": i + 1, "cumulative_pct": round(cumsum / total * 100, 3)})
    return pts


def _correlation_matrix(labels: list[str]):
    """Generate a realistic (symmetric) correlation matrix."""
    n = len(labels)
    mat = [[1.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            # Features from same class tend to correlate
            base = 0.6 if (i // 3 == j // 3) else 0.0
            val = round(max(-1.0, min(1.0, base + random.gauss(0, 0.25))), 3)
            mat[i][j] = val
            mat[j][i] = val
    return mat


def _scatter_points(n: int = 120, task: str = "classification", n_labels: int = 2):
    classes = _generate_labels(n_labels)
    pts = []

    per_class = n // n_labels

    for i, cls in enumerate(classes):
        angle = 2 * math.pi * i / n_labels
        cx = 3 * math.cos(angle)
        cy = 3 * math.sin(angle)

        for _ in range(per_class):
            pts.append({
                "x": round(cx + random.gauss(0, 1.2), 4),
                "y": round(cy + random.gauss(0, 1.2), 4),
                "target": cls,
            })

    random.shuffle(pts)
    return pts

def _confusion_matrix(n_labels: int = 2):
    mat = []
    for i in range(n_labels):
        row = []
        for j in range(n_labels):
            val = random.randint(0, 50)
            if i == j:
                val += 40
            row.append(val)
        mat.append(row)
    return mat


# ── Dummy payload builders ─────────────────────────────────────────────────────

def build_modeling_payload(n_labels: int) -> dict:
    """
    Mimics what pycaret_train_classifier returns (after the agent tool call),
    as processed by _emit_tool_result_to_view → emit_modeling_output.
    """
    model_specs = [
        {"key": "tuned_model_1", "label": "Random Forest",      "auc": 0.91, "acc": 0.87, "f1": 0.86, "prec": 0.88, "rec": 0.85},
        {"key": "tuned_model_2", "label": "XGBoost",            "auc": 0.89, "acc": 0.85, "f1": 0.84, "prec": 0.86, "rec": 0.83},
        {"key": "tuned_model_3", "label": "Logistic Regression","auc": 0.84, "acc": 0.80, "f1": 0.79, "prec": 0.81, "rec": 0.78},
        {"key": "blended_model", "label": "Blended Ensemble",   "auc": 0.93, "acc": 0.89, "f1": 0.88, "prec": 0.90, "rec": 0.87},
    ]

    # Comparison table (mimics PyCaret compare_models pull())
    comparison_table = [
        {
            "Model":     m["label"],
            "Accuracy":  round(m["acc"] + random.gauss(0, 0.005), 4),
            "AUC":       round(m["auc"] + random.gauss(0, 0.005), 4),
            "Recall":    round(m["rec"] + random.gauss(0, 0.005), 4),
            "Precision": round(m["prec"] + random.gauss(0, 0.005), 4),
            "F1":        round(m["f1"] + random.gauss(0, 0.005), 4),
        }
        for m in model_specs
    ]
    comparison_table.sort(key=lambda r: r["AUC"], reverse=True)

    # Per-model detail
    per_model = {}
    for m in model_specs:
        per_model[m["key"]] = {
            "label": m["label"],
            "cv_metrics": {
                "Accuracy":  round(m["acc"] - 0.01 + random.gauss(0, 0.003), 4),
                "AUC":       round(m["auc"] - 0.01 + random.gauss(0, 0.003), 4),
                "F1":        round(m["f1"]  - 0.01 + random.gauss(0, 0.003), 4),
                "Precision": round(m["prec"]- 0.01 + random.gauss(0, 0.003), 4),
                "Recall":    round(m["rec"] - 0.01 + random.gauss(0, 0.003), 4),
            },
            "holdout_metrics": {
                "accuracy":    round(m["acc"] + random.gauss(0, 0.005), 4),
                "auc":         round(m["auc"] + random.gauss(0, 0.005), 4),
                "f1":          round(m["f1"]  + random.gauss(0, 0.005), 4),
                "precision":   round(m["prec"]+ random.gauss(0, 0.005), 4),
                "recall":      round(m["rec"] + random.gauss(0, 0.005), 4),
                # "specificity": round(tn / (tn + fp), 4),
                # "true_positives":  tp,
                # "false_positives": fp,
                # "true_negatives":  tn,
                # "false_negatives": fn,
            },
            "confusion_matrix": _confusion_matrix(n_labels),
            "confusion_matrix_labels": _generate_labels(n_labels),
            "roc_curve": _roc_curve(m["auc"]),
            "pr_curve":  _pr_curve(m["auc"]),
        }

    return {
        "tool":   "classification",
        "status": "success",
        "experiment_name":        "radiomics_classification_exp",
        "output_dir":             "/workspace/modeling",
        "final_model_path":       "/workspace/modeling/radiomics_classification_exp_final_model.pkl",
        "individual_model_paths": {m["key"]: f"/workspace/modeling/models/{m['key']}/{m['key']}.pkl" for m in model_specs},
        "model_test_predictions": {m["key"]: f"/workspace/modeling/models/{m['key']}/holdout_predictions.csv" for m in model_specs},
        "model_summary":          "/workspace/modeling/model_summary.csv",
        "created_files": [
            "/workspace/modeling/model_comparison_results.csv",
            "/workspace/modeling/model_summary.csv",
        ],
        "chart_data": {
            "comparison_table": comparison_table,
            "per_model": per_model,
            "fold": 5,
        },
    }


def build_features_extraction_payload(n_labels: int) -> dict:
    """Mimics radiomics_extract_features tool return."""
    labels = _generate_labels(n_labels)
    return {
        "tool":   "radiomic_extraction",
        "status": "success",
        "csv_paths": {
            f"label_{l}": f"/workspace/features/radiomic_features_label_{l}.csv"
            for l in labels
        },
        "num_subjects": 87,
        "num_features": {str(l): 851 for l in labels},
        "mask_labels": labels,
        "processing_time_seconds": 142.8,
    }


def build_features_importance_payload(n_labels: int) -> dict:
    """
    Mimics radiomics_analyze_importance tool return (after the agent tool call),
    as processed by _emit_tool_result_to_view → emit_features_output.
    """
    # Realistic radiomic feature names
    feature_prefixes = [
        "original_firstorder", "original_glcm", "original_glrlm",
        "original_shape", "wavelet-LH_glcm", "wavelet-HL_glcm",
        "wavelet-HH_firstorder", "original_gldm", "original_ngtdm",
    ]
    feature_names_pool = []
    suffixes = [
        "Mean", "Variance", "Skewness", "Kurtosis", "Energy",
        "Entropy", "Contrast", "Correlation", "DifferenceEntropy",
        "Sphericity", "Flatness", "Elongation", "MeshVolume",
        "RunLengthNonUniformity", "GrayLevelNonUniformity",
        "Coarseness", "Busyness", "Complexity",
    ]
    for prefix in feature_prefixes:
        for suffix in suffixes[:6]:
            feature_names_pool.append(f"{prefix}_{suffix}")

    # 30 top features with decaying importance
    n_bars = 30
    top_features = random.sample(feature_names_pool, min(n_bars, len(feature_names_pool)))
    importances = sorted(
        [abs(random.gauss(0, 1)) * (1 / (i + 1) ** 0.6) for i in range(n_bars)],
        reverse=True,
    )
    importance_bars = [
        {"feature": f, "importance": round(importances[i], 6)}
        for i, f in enumerate(top_features)
    ]

    # Correlation matrix (top 20)
    corr_labels = top_features[:20]
    corr_matrix = _correlation_matrix(corr_labels)

    # Scatter points
    pca_points = _scatter_points(120, n_labels)
    tsne_points = _scatter_points(120, n_labels)
    # t-SNE clusters more tightly
    tsne_points = [
        {"x": round(p["x"] * 8 + random.gauss(0, 0.5), 4),
         "y": round(p["y"] * 8 + random.gauss(0, 0.5), 4),
         "target": p["target"]}
        for p in tsne_points
    ]

    return {
        "tool":          "feature_importance",
        "status":        "success",
        "method":        "rf",
        "feature_count": 851,
        "result_files": {
            "importance": "/workspace/features/feature_importance.csv",
            "selected_features": {
                "10":  "/workspace/features/top_10_features.csv",
                "50":  "/workspace/features/top_50_features.csv",
                "100": "/workspace/features/top_100_features.csv",
            },
        },
        "task_type": "classification",
        "chart_data": {
            "task_type": "classification",
            "method":    "rf",
            "importance_bars": importance_bars,
            "cumulative":      _cumulative(851),
            "correlation": {
                "labels": corr_labels,
                "matrix": corr_matrix,
            },
            "pca": {
                "points":              pca_points,
                "explained_variance":  [0.312, 0.187],
                "n_features":          100,
            },
            "tsne": {
                "points":    tsne_points,
                "n_features": 100,
            },
        },
    }

# ── helpers ─────────────────────────────────────────────────────────────

def _generate_labels(n_labels: int):
    return [str(i) for i in range(n_labels)]


# ── HTTP broadcast ─────────────────────────────────────────────────────────────

async def broadcast(client: httpx.AsyncClient, base_url: str, msg_type: str, payload: dict, session_id: str):
    """POST to /ws-broadcast — same path the app uses internally."""
    body = {"type": msg_type, "payload": payload, "session_id": session_id}
    try:
        r = await client.post(f"{base_url}/ws-broadcast", json=body, timeout=10)
        r.raise_for_status()
        print(f"  ✓  {msg_type}")
    except httpx.HTTPStatusError as e:
        print(f"  ✗  {msg_type}  →  HTTP {e.response.status_code}: {e.response.text[:120]}")
    except httpx.RequestError as e:
        print(f"  ✗  {msg_type}  →  Connection error: {e}")
        sys.exit(1)


# ── Emit sequences ─────────────────────────────────────────────────────────────

async def emit_modeling(client: httpx.AsyncClient, base_url: str, session_id: str, delay: float, n_labels: int):
    print("\n▶  Emitting MODELING payloads …")

    payload = build_modeling_payload(n_labels)
    await broadcast(client, base_url, "modeling_output", payload, session_id)
    print(f"   Sent full modeling payload with {len(payload['chart_data']['per_model'])} models")


async def emit_features(client: httpx.AsyncClient, base_url: str, session_id: str, delay: float, n_labels: int):
    print("\n▶  Emitting FEATURES payloads …")

    # Step 1 — extraction results (populates the right-panel metadata)
    extraction = build_features_extraction_payload(n_labels)
    await broadcast(client, base_url, "features_output", extraction, session_id)
    print(f"   Sent extraction payload  ({extraction['num_subjects']} subjects, {sum(extraction['num_features'].values())} features)")

    if delay > 0:
        print(f"   Waiting {delay}s before importance payload …")
        await asyncio.sleep(delay)

    # Step 2 — importance analysis (populates all chart tabs)
    importance = build_features_importance_payload(n_labels)
    await broadcast(client, base_url, "features_output", importance, session_id)
    n_bars = len(importance["chart_data"]["importance_bars"])
    n_cum  = len(importance["chart_data"]["cumulative"])
    print(f"   Sent importance payload  ({n_bars} bars, {n_cum} cumulative pts, PCA+t-SNE included)")


# ── Entry point ────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Emit dummy frontend view payloads for testing.")
    parser.add_argument("--host",    default=DEFAULT_HOST,    help=f"API host (default: {DEFAULT_HOST})")
    parser.add_argument("--port",    default=DEFAULT_PORT,    type=int, help=f"API port (default: {DEFAULT_PORT})")
    parser.add_argument("--session", default=DEFAULT_SESSION, help=f"Session ID (default: {DEFAULT_SESSION})")
    parser.add_argument("--views",   default="all",           choices=["all", "modeling", "features"],
                        help="Which views to populate (default: all)")
    parser.add_argument("--delay",   default=2.0,             type=float,
                        help="Seconds between extraction and importance payloads for features (default: 2)")
    parser.add_argument("--labels", default=2, type=int, help="Number of labels/classes (default: 2)")
    args = parser.parse_args()

    base_url   = f"http://{args.host}:{args.port}"
    session_id = args.session

    print(f"\n{'─'*55}")
    print(f"  Frontend View Test Script")
    print(f"{'─'*55}")
    print(f"  Target   : {base_url}")
    print(f"  Session  : {session_id}")
    print(f"  Views    : {args.views}")
    print(f"  Labels   : {args.labels}")
    print(f"{'─'*55}")

    async with httpx.AsyncClient() as client:
        # Health-check
        try:
            r = await client.get(f"{base_url}/status/{session_id}", timeout=5)
        except httpx.RequestError:
            print(f"\n✗  Cannot reach {base_url}. Is the FastAPI server running?\n")
            sys.exit(1)

        if args.views in ("all", "features"):
            await emit_features(client, base_url, session_id, delay=args.delay, n_labels=args.labels)

        if args.views in ("all", "modeling"):
            await emit_modeling(client, base_url, session_id, delay=0, n_labels=args.labels)

    print(f"\n✓  Done. Open the playground and navigate to the relevant view tab.\n")


if __name__ == "__main__":
    asyncio.run(main())
