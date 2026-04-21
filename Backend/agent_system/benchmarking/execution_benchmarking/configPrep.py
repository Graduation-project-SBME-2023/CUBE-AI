
BENCHMARK_CASES: Dict[str, Dict[str, Any]] = {
    "heart": {
        "name": "Heart MRI — Cardiomyopathies vs Normal",
        "paper": "Zhang et al. (2023) - Cardiac magnetic resonance radiomics for disease classification",
        "pubmed": "https://pubmed.ncbi.nlm.nih.gov/36378251/",
        "dataset": "ACDC Challenge (283 subjects: 48 HCM, 52 DCM, 123 healthy)",
        "prompt": (
            "cardiomyopathy? I want to use cine CMR images to distinguish "
            "hypertrophic cardiomyopathy, dilated cardiomyopathy, and normal "
            "hearts using texture and shape features with machine learning."
        ),
        "ground_truth": {
            "organ": "Heart",
            "modality": "MRI (cine CMR)",
            "modality_detail": "cine CMR / SSFP",
            "pathologies": ["Hypertrophic Cardiomyopathy (HCM)", "Dilated Cardiomyopathy (DCM)", "Normal"],
            "best_model": "Random Forest + mRMR",
            "best_accuracy": 0.912,
            "best_aucs": {"Normal": 0.938, "HCM": 0.966, "DCM": 0.936},
            "n_subjects": 283,
            "task_type": "multi-class classification",
            "expected_agents_required": [
                "study_population_planner_agent",
                "segmentation_agent",
                "feature_extraction_agent",
                "modeling_agent",
                "result_reporter_agent",
            ],
            "expected_agents_optional": ["latex_reporter_agent"],
        },
    },
    "liver": {
        "name": "Liver CT — Tumor vs Normal",
        "paper": "Lysdahlgaard et al. (2022) - Comparing Radiomics features of tumour and healthy liver tissue",
        "pubmed": "https://www.sciencedirect.com/science/article/pii/S1078817422000499",
        "dataset": "LiTS Challenge (131 contrast-enhanced liver CT scans)",
        "prompt": (
            "Can CT radiomics distinguish hepatocellular carcinoma from normal "
            "liver tissue? I want to extract texture and intensity features from "
            "contrast-enhanced CT scans and classify tumor versus healthy liver "
            "parenchyma using machine learning."
        ),
        "ground_truth": {
            "organ": "Liver",
            "modality": "CT (contrast-enhanced)",
            "modality_detail": "contrast-enhanced CT",
            "pathologies": ["Hepatocellular Carcinoma (HCC)", "Normal liver"],
            "best_model": "Logistic Regression / Random Forest / SVM",
            "best_accuracy": 0.96,
            "n_subjects": 131,
            "task_type": "binary classification",
            "expected_agents_required": [
                "study_population_planner_agent",
                "segmentation_agent",
                "feature_extraction_agent",
                "modeling_agent",
                "result_reporter_agent",
            ],
            "expected_agents_optional": ["latex_reporter_agent"],
        },
    },
    "prostate": {
        "name": "Prostate MRI — Cancer vs Normal",
        "paper": "Kim et al. (2025) - Radiomics Reproducibility in Prostate Cancer Diagnosis",
        "pubmed": "https://pmc.ncbi.nlm.nih.gov/articles/PMC12688314/",
        "dataset": "PROSTATEx Challenge (82 men: 41 cancer, 41 normal)",
        "prompt": (
            "Can multiparametric MRI radiomics detect prostate cancer? I want to "
            "extract features from T2-weighted and ADC maps of prostate MRI to "
            "classify cancer versus normal tissue, focusing on reproducible "
            "features with high ICC."
        ),
        "ground_truth": {
            "organ": "Prostate",
            "modality": "MRI (mpMRI: T2W + ADC)",
            "modality_detail": "mpMRI (T2W + ADC)",
            "pathologies": ["Prostate Cancer", "Normal"],
            "best_model": "SVM / Neural Net / Logistic Regression",
            "best_accuracy": 0.84,
            "best_auc": 0.85,
            "n_subjects": 82,
            "task_type": "binary classification",
            "expected_agents_required": [
                "study_population_planner_agent",
                "segmentation_agent",
                "feature_extraction_agent",
                "modeling_agent",
                "result_reporter_agent",
            ],
            "expected_agents_optional": ["latex_reporter_agent"],
        },
    },
    "kidney": {
        "name": "Kidney CT — Tumor vs Normal",
        "paper": "Li et al. (2023) - Automatic classification of kidney tumor and normal kidney tissue",
        "pubmed": "https://pmc.ncbi.nlm.nih.gov/articles/PMC9998940/",
        "dataset": "KiTS19 / C4KC-KITS (210 patients: 168 train, 42 test)",
        "prompt": (
            "Can CT radiomics classify kidney tumors versus normal kidney tissue? "
            "I want to extract radiomic features from arterial-phase CT scans of "
            "renal masses and contralateral normal kidneys to build a classification "
            "model for automatic kidney tumor detection."
        ),
        "ground_truth": {
            "organ": "Kidney",
            "modality": "CT (arterial phase)",
            "modality_detail": "arterial-phase CT",
            "pathologies": ["Renal Cell Carcinoma", "Normal kidney"],
            "best_model": "Logistic Regression (3 features)",
            "best_accuracy": 0.85,
            "best_auc": 0.85,
            "n_subjects": 210,
            "task_type": "binary classification",
            "expected_agents_required": [
                "study_population_planner_agent",
                "segmentation_agent",
                "feature_extraction_agent",
                "modeling_agent",
                "result_reporter_agent",
            ],
            "expected_agents_optional": ["latex_reporter_agent"],
        },
    },
}
