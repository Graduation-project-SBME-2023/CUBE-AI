"""PICO concept extraction for the literature tools (merged plan, Phase 2.4 step 4).

Copied from ``agents/literature_review_agent/knowledge_builder.py`` (the term
dictionaries and ``extract_concepts_from_pico``), which goes with the AutoGen
literature agent in Plan 17. The search tools filter their results with the
domain keywords ``domain_keywords()`` derives from a PICO.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_ORGAN_TERMS: Dict[str, str] = {
    "spleen": "Spleen", "splenic": "Spleen",
    "liver": "Liver", "hepatic": "Liver", "hepatocellular": "Liver",
    "kidney": "Kidney", "renal": "Kidney", "nephrotic": "Kidney",
    "lung": "Lung", "pulmonary": "Lung", "bronchial": "Lung",
    "brain": "Brain", "cerebral": "Brain", "intracranial": "Brain",
    "breast": "Breast", "mammary": "Breast", "mammographic": "Breast",
    "prostate": "Prostate", "prostatic": "Prostate",
    "pancreas": "Pancreas", "pancreatic": "Pancreas",
    "colon": "Colon", "colorectal": "Colon", "rectal": "Rectum",
    "thyroid": "Thyroid", "cardiac": "Heart", "heart": "Heart",
    "bone": "Bone", "skeletal": "Bone", "osseous": "Bone",
    "ovary": "Ovary", "ovarian": "Ovary",
    "stomach": "Stomach", "gastric": "Stomach",
    "esophagus": "Esophagus", "oesophagus": "Esophagus",
    "bladder": "Urinary Bladder", "adrenal": "Adrenal Glands",
    "lymph node": "Lymph Nodes", "lymph nodes": "Lymph Nodes",
}

_PATHOLOGY_TERMS: Dict[str, str] = {
    "lymphoma": "Lymphoma", "hodgkin": "Hodgkin Lymphoma",
    "non-hodgkin": "Non-Hodgkin Lymphoma", "dlbcl": "Diffuse Large B-Cell Lymphoma",
    "follicular": "Follicular Lymphoma", "marginal zone": "Marginal Zone Lymphoma",
    "mantle cell": "Mantle Cell Lymphoma", "burkitt": "Burkitt Lymphoma",
    "leukaemia": "Leukemia", "leukemia": "Leukemia",
    "metastasis": "Neoplasm Metastasis", "metastases": "Neoplasm Metastasis",
    "metastatic": "Neoplasm Metastasis",
    "carcinoma": "Carcinoma", "adenocarcinoma": "Adenocarcinoma",
    "melanoma": "Melanoma", "sarcoma": "Sarcoma",
    "glioma": "Glioma", "glioblastoma": "Glioblastoma",
    "hepatocellular carcinoma": "Hepatocellular Carcinoma", "hcc": "Hepatocellular Carcinoma",
    "renal cell carcinoma": "Renal Cell Carcinoma",
    "angiosarcoma": "Angiosarcoma",
    "haemangioma": "Hemangioma", "hemangioma": "Hemangioma",
    "hamartoma": "Hamartoma",
    "abscess": "Abscess", "infarct": "Infarction",
    "benign": "Benign Neoplasm", "malignant": "Malignant Neoplasm",
    "cancer": "Neoplasms", "tumour": "Neoplasms", "tumor": "Neoplasms",
    "neoplasm": "Neoplasms", "lesion": "Pathologic Processes",
    "cyst": "Cysts", "fibrosis": "Fibrosis",
    "splenomegaly": "Splenomegaly", "hepatomegaly": "Hepatomegaly",
}

_MODALITY_TERMS: Dict[str, str] = {
    "ct": "Computed Tomography", "computed tomography": "Computed Tomography",
    "mri": "Magnetic Resonance Imaging", "magnetic resonance": "Magnetic Resonance Imaging",
    "pet-ct": "PET-CT", "pet/ct": "PET-CT", "pet ct": "PET-CT",
    "pet-mri": "PET-MRI", "pet/mri": "PET-MRI",
    "ultrasound": "Ultrasound", "sonography": "Ultrasound",
    "mammography": "Mammography", "x-ray": "X-Ray Radiography",
    "fluoroscopy": "Fluoroscopy", "nuclear medicine": "Nuclear Medicine",
    "spect": "SPECT", "oct": "Optical Coherence Tomography",
}

_METHOD_TERMS: Dict[str, str] = {
    "radiomics": "Radiomics", "radiomic": "Radiomics",
    "texture analysis": "Texture Analysis", "texture feature": "Texture Analysis",
    "machine learning": "Machine Learning", "deep learning": "Deep Learning",
    "random forest": "Random Forest", "svm": "Support Vector Machine",
    "support vector": "Support Vector Machine",
    "xgboost": "Gradient Boosting", "gradient boosting": "Gradient Boosting",
    "logistic regression": "Logistic Regression",
    "neural network": "Neural Networks", "cnn": "Convolutional Neural Network",
    "convolutional": "Convolutional Neural Network",
    "transformer": "Vision Transformer", "vision transformer": "Vision Transformer",
    "u-net": "U-Net Segmentation", "unet": "U-Net Segmentation",
    "segmentation": "Image Segmentation", "classification": "Classification",
    "feature extraction": "Feature Extraction", "feature selection": "Feature Selection",
    "glcm": "GLCM Texture Features", "glrlm": "GLRLM Texture Features",
    "glszm": "GLSZM Texture Features", "gldm": "GLDM Texture Features",
    "wavelet": "Wavelet Features", "shape feature": "Shape Features",
    "first-order": "First-Order Statistics", "histogram": "Histogram Features",
    "ibsi": "IBSI Standard", "pyradiomics": "PyRadiomics",
    "lasso": "LASSO Regression", "mrmr": "mRMR Feature Selection",
    "shap": "SHAP Explainability", "grad-cam": "Grad-CAM",
    "smote": "SMOTE Oversampling", "cross-validation": "Cross-Validation",
    "transfer learning": "Transfer Learning",
    "nomogram": "Nomogram", "rad-score": "Radiomics Score",
}

_METRIC_TERMS: Dict[str, str] = {
    "auc": "AUC-ROC", "roc": "ROC Curve", "auc-roc": "AUC-ROC",
    "sensitivity": "Sensitivity", "specificity": "Specificity",
    "accuracy": "Accuracy", "precision": "Precision", "recall": "Recall",
    "f1": "F1 Score", "ppv": "Positive Predictive Value",
    "npv": "Negative Predictive Value",
    "icc": "Intraclass Correlation Coefficient",
    "dice": "Dice Similarity Coefficient",
    "hausdorff": "Hausdorff Distance",
}


def extract_concepts_from_pico(pico: Dict[str, str]) -> Dict[str, List[str]]:
    """
    Extract key medical/technical concepts from PICO input.

    Returns a dict with keys: organs, pathologies, modalities, methods, metrics.
    Each value is a deduplicated list of canonical concept names.
    """
    all_text = " ".join(pico.values()).lower()

    def _match(term_dict: Dict[str, str]) -> List[str]:
        found = []
        for term, canonical in term_dict.items():
            # Use word boundary matching for short terms to avoid false positives
            if len(term) <= 3:
                if re.search(rf"\b{re.escape(term)}\b", all_text):
                    if canonical not in found:
                        found.append(canonical)
            else:
                if term in all_text and canonical not in found:
                    found.append(canonical)
        return found

    concepts = {
        "organs": _match(_ORGAN_TERMS),
        "pathologies": _match(_PATHOLOGY_TERMS),
        "modalities": _match(_MODALITY_TERMS),
        "methods": _match(_METHOD_TERMS),
        "metrics": _match(_METRIC_TERMS),
    }

    logger.info(
        f"Extracted concepts — organs: {concepts['organs']}, "
        f"pathologies: {len(concepts['pathologies'])}, "
        f"modalities: {concepts['modalities']}, "
        f"methods: {len(concepts['methods'])}"
    )
    return concepts


# A broad catch-all for any medical imaging research, used when a PICO yields
# too few concepts (copied from literature_review_agent.py).
DOMAIN_KEYWORDS_FALLBACK: List[str] = [
    "imaging", "CT", "MRI", "tomography", "radiology", "oncology",
    "radiomics", "texture", "malignancy", "cancer", "tumour", "tumor",
    "classification", "segmentation", "detection", "diagnostic", "pathology",
    "feature extraction", "machine learning", "deep learning", "neural network",
    "random forest", "clinical", "biomarker", "lesion", "neoplasm",
]


def domain_keywords(pico: Optional[Dict[str, str]]) -> List[str]:
    """PICO-derived keywords plus the fallback list; the fallback alone when
    the PICO yields fewer than four concepts (``_build_domain_keywords``)."""
    if not pico:
        return list(DOMAIN_KEYWORDS_FALLBACK)
    try:
        concepts = extract_concepts_from_pico(pico)
    except Exception:
        logger.exception("could not extract concepts from the PICO; using the fallback keywords")
        return list(DOMAIN_KEYWORDS_FALLBACK)
    seen: set[str] = set()
    unique: List[str] = []
    for keyword in (
        *concepts.get("organs", []),
        *concepts.get("pathologies", []),
        *concepts.get("modalities", []),
        *concepts.get("methods", []),
    ):
        if keyword.lower() not in seen:
            seen.add(keyword.lower())
            unique.append(keyword)
    if len(unique) < 4:
        return list(DOMAIN_KEYWORDS_FALLBACK)
    return unique + [kw for kw in DOMAIN_KEYWORDS_FALLBACK if kw.lower() not in seen]


def is_domain_relevant(title: str, abstract: str, keywords: List[str]) -> bool:
    """True when the paper mentions at least two of ``keywords``."""
    text = f"{title} {abstract}".lower()
    return sum(1 for kw in keywords if kw.lower() in text) >= 2

