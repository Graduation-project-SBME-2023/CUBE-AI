# Literature Review: Radiomics Features for Detection and Classification of Cancerous Splenic Involvement

**Generated:** 2024-12-19T10:30:00Z
**Pipeline Version:** 2.0
**Total Papers Reviewed:** 4 | **Highly Relevant:** 2 | **Similar Studies:** 1

---

## 1. Executive Summary

The field of splenic radiomics research is still in its early stages, with limited published literature specifically addressing the use of radiomics for detecting cancerous splenic involvement. The retrieved literature indicates that while automated spleen segmentation using deep learning models has shown promising results, research focusing specifically on radiomic feature analysis for malignant vs benign splenic pathology differentiation remains scarce.

Current evidence primarily demonstrates the feasibility of deep learning-based spleen segmentation and classification of splenomegaly etiology (portal hypertension vs lymphoma) [R1]. However, there is a notable gap in studies that specifically extract and analyze radiomics features (GLCM, GLRLM, shape descriptors) for cancerous splenic involvement detection. The most relevant studies focus instead on prognostic applications of spleen radiomics in other cancers like cervical cancer and esophageal cancer [R3, R4], rather than diagnostic classification of splenic malignancies themselves.

Critical gaps exist in establishing standardized radiomics pipelines for splenic malignancy detection, validating discriminative power of specific feature categories, and comparing radiomics performance against conventional radiological assessment. The current hypothesis requires further investigation with dedicated studies focusing specifically on splenic cancer detection rather than repurposing spleen radiomics for prognostic applications in other malignancies.

---

## 2. PICO Framework Analysis

### 2.1 Population

The retrieved literature demonstrates limited focus on the target population of patients with suspected or confirmed splenic malignancies. Only one study [R1] included lymphoma patients specifically, while studies [R3, R4] focused on patients with cervical and esophageal cancers using spleen radiomics for prognostic purposes rather than splenic malignancy diagnosis. Cohort sizes in similar studies ranged from 149-257 patients, but these were not primarily focused on splenic cancer detection.

### 2.2 Intervention

The literature shows adoption of deep learning segmentation methods (two-stage U-Net models) [R1] but limited evidence of comprehensive radiomics feature extraction pipelines targeting splenic cancer. Studies utilized LASSO-Cox regression for feature selection in prognostic models [R3, R4], but specific IBSI-compliant feature categories for splenic malignancy classification were not thoroughly investigated.

### 2.3 Comparison

No studies were identified that directly compared radiomics against conventional radiological assessment for splenic malignancy detection. The comparison dimension remains largely unexplored in the retrieved literature, with most studies focusing on prognostic outcomes rather than diagnostic accuracy comparisons.

### 2.4 Outcome

Diagnostic performance metrics specific to splenic cancer detection were not reported in the retrieved literature. Studies reported prognostic outcomes (progression-free survival prediction) with radiomics scores but did not provide AUC-ROC, sensitivity, or specificity metrics for malignant vs benign classification of splenic lesions.

---

## 3. Thematic Analysis

### 3.1 Radiomic Feature Engineering & IBSI Standardisation

The retrieved literature demonstrates limited application of standardized radiomics pipelines for splenic malignancy detection. While studies [R3, R4] employed radiomic feature extraction for prognostic modeling, they did not specifically address IBSI compliance or feature stability across imaging protocols. The focus was primarily on predictive modeling rather than feature engineering standardization.

### 3.2 Splenic Segmentation Methods & Reproducibility

Study [R1] provides the most relevant evidence for automated spleen segmentation using a two-stage deep learning model achieving automated segmentation and classification of splenomegaly etiology. The model demonstrated feasibility in distinguishing between cirrhotic portal hypertension and lymphoma-related splenomegaly, suggesting potential applicability to malignancy detection.

### 3.3 CT/MRI Acquisition Protocol Optimisation

No studies specifically addressing protocol optimization for splenic radiomics were retrieved. The literature gap in this dimension highlights the need for standardized acquisition protocols tailored to splenic texture analysis.

### 3.4 ML/DL Classifier Performance & Feature Selection

Studies [R3, R4] utilized LASSO-Cox regression for feature selection in prognostic models, while [R1] employed deep learning classification. However, these applications were for etiology classification and prognosis prediction rather than malignant vs benign differentiation of splenic lesions.

### 3.5 Diagnostic Performance Benchmarking

Substantial gaps exist in diagnostic performance benchmarking for splenic malignancy detection. The retrieved literature focused on prognostic outcomes rather than diagnostic accuracy metrics, indicating a critical research need in this area.

### 3.6 Comparative Analysis vs. Conventional Assessment

No comparative studies were found examining radiomics performance against conventional radiological assessment for splenic cancer detection. This represents a significant evidence gap in the literature.

### 3.7 Multi-centre Validation & Generalisation

The retrieved studies were single-center retrospective designs, lacking multi-center validation. Study [R2] addressed validation challenges in multi-organ segmentation but did not specifically focus on spleen or malignancy detection.

### 3.8 Clinical Translation & Workflow Integration

Limited evidence exists regarding clinical translation of splenic radiomics for malignancy detection. The current applications are primarily research-focused with minimal workflow integration insights.

---

## 4. Most Similar Prior Studies

| Rank | Title (shortened) | Authors | Year | Similarity Reason | Score |
|------|-------------------|---------|------|-------------------|-------|
| [R1] | Two-Stage Deep Learning Model for Automated Segmentation and Classification of Splenomegaly | Meddeb et al. | 2022 | Directly addresses spleen classification including lymphoma | 12.29 |

**Study [R1] Analysis:** This study represents the closest methodological approach to the research hypothesis, employing deep learning for spleen segmentation and classification specifically including lymphoma patients. The two-stage model demonstrates feasibility in automated splenic analysis but focuses on splenomegaly etiology classification rather than radiomics feature extraction for malignancy detection.

---

## 5. Research Gaps & Open Questions

- **Standardized radiomics pipeline for splenic malignancy detection** - Current literature lacks dedicated studies establishing IBSI-compliant radiomics workflows specifically for splenic cancer detection, requiring development of optimized feature extraction protocols.

- **Diagnostic performance validation** - No studies reported AUC-ROC, sensitivity, or specificity metrics for malignant vs benign splenic lesion classification, creating a critical evidence gap for clinical translation.

- **Comparative effectiveness against conventional assessment** - The absence of head-to-head comparisons between radiomics and radiologist assessment prevents evaluation of incremental diagnostic value.

- **Multi-center generalizability** - All retrieved studies were single-center designs, limiting understanding of protocol harmonization and feature stability across institutions.

- **Feature category discriminative power analysis** - Specific investigation of which radiomic feature categories (texture, shape, intensity) provide optimal discriminative power for splenic malignancies is missing.

- **Clinical workflow integration pathways** - Practical implementation strategies for integrating splenic radiomics into routine clinical workflows remain unexplored.

---

## 6. Methodological Recommendations

### 6.1 Imaging Protocol Specification
Given the literature gaps, recommend standardized portal venous phase CT with 1-2mm slice thickness reconstruction, consistent with abdominal oncology protocols. Multi-phase CT acquisition should be considered for temporal feature analysis.

### 6.2 Segmentation Strategy
Build upon the two-stage U-Net approach demonstrated in [R1], employing manual refinement by expert radiologists with target ICC > 0.90 for segmentation reproducibility.

### 6.3 Feature Extraction & Selection
Implement IBSI-compliant feature extraction using PyRadiomics with comprehensive feature categories including wavelet decompositions. LASSO regularization is recommended based on its successful application in prognostic models [R3, R4].

### 6.4 Model Architecture & Training
Employ Random Forest or XGBoost classifiers with stratified k-fold cross-validation, incorporating SMOTE for class imbalance handling given the likely scarcity of malignant cases.

### 6.5 Validation & Reporting Standards
Adhere to TRIPOD reporting guidelines and RQS scoring framework. Plan for external validation cohort and include decision curve analysis for clinical utility assessment.

---

## 7. Reference Sources for Further Investigation

### Peer-Reviewed Journals
| Journal | Publisher | Impact Factor | Relevance |
|---------|-----------|---------------|-----------|
| Radiology | RSNA | ~20 | Primary outlet for clinical AI imaging |
| European Radiology | Springer | ~7 | European clinical radiology AI |
| Radiology: Artificial Intelligence | RSNA | ~8 | AI-specific radiology |
| Medical Image Analysis | Elsevier | ~14 | Technical radiomics methods |
| Cancer Imaging | BioMed Central | ~4 | Oncology-focused imaging research |

### Clinical Guidelines & Standards
- **ACR Appropriateness Criteria**: https://acsearch.acr.org
- **ESMO Haematology Guidelines**: https://www.esmo.org/guidelines
- **NCCN B-Cell Lymphoma**: https://www.nccn.org/guidelines
- **IBSI 2.0 Standard**: https://ibsi.netlify.app
- **TRIPOD Reporting Statement**: https://www.equator-network.org

### Research Databases
- **PubMed/MEDLINE**: https://pubmed.ncbi.nlm.nih.gov
- **Semantic Scholar**: https://www.semanticscholar.org
- **TCIA (imaging datasets)**: https://tcia.nci.nih.gov
- **Europe PMC**: https://europepmc.org

---

## 8. Ranked Bibliography

| Rank | Score | Title | Authors | Year | Journal | Source | URL |
|------|-------|-------|---------|------|---------|--------|-----|
| 1 | 12.29 | Two-Stage Deep Learning Model for Automated Segmentation and Classification of Splenomegaly | Meddeb Aymen, Kossen Tabea, Bressem Keno K, Molinski Noah, Hamm Bernd et al. | 2022 | Cancers | PubMed | [Link](https://pubmed.ncbi.nlm.nih.gov/36428569/) |
| 2 | 8.42 | Systemic immune-related spleen radiomics predict progression-free survival in patients with locally advanced cervical cancer underwent definitive chemoradiotherapy | Li Yi, Guo Longxiang, Xie Peng, Liu Yuhui, Li Yuanlin et al. | 2024 | BMC medical imaging | PubMed | [Link](https://pubmed.ncbi.nlm.nih.gov/39548404/) |
| 3 | 7.89 | The role of spleen radiomics model for predicting prognosis in esophageal squamous cell carcinoma patients receiving definitive radiotherapy | Guo Longxiang, Liu Ao, Geng Xiaotao, Zhao Zongxing, Nie Yu et al. | 2024 | Thoracic cancer | PubMed | [Link](https://pubmed.ncbi.nlm.nih.gov/38480505/) |
| 4 | 6.55 | Validation and Optimization of Multi-Organ Segmentation on Clinical Imaging Archives | Tang Olivia, Xu Yuchen, Tang Yucheng, Lee Ho Hin, Chen Yunqiang et al. | 2020 | Proceedings of SPIE | PubMed | [Link](https://pubmed.ncbi.nlm.nih.gov/34040277/) |