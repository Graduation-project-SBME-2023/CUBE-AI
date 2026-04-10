from typing import Dict, Callable, Awaitable, Any
from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_ext.models.ollama import OllamaChatCompletionClient

def create_model_client(api_key: str, model: str = "gpt-oss:120b-cloud"):
    return OllamaChatCompletionClient(
        model=model,
        model_info={
            "vision": False,
            "function_calling": True,
            "json_output": True,
        },
        api_key=api_key
    )

# ══════════════════════════════════════════════════════════════════════════════
# AVAILABLE PIPELINE AGENTS
# ══════════════════════════════════════════════════════════════════════════════
# These are ALL the execution agents the system can schedule.
# The Pipeline Architect dynamically selects which ones are needed.
AVAILABLE_AGENTS = [
    {
        "id": "study_population_planner_agent",
        "title": "Cohort Discovery",
        "description": "Query PACS, select study population, define inclusion/exclusion criteria, generate targets",
        "capabilities": [
            "DICOM query/retrieve from Orthanc PACS",
            "Patient cohort selection with inclusion/exclusion criteria",
            "Target label generation (targets.csv) for downstream ML",
            "Demographic analysis and sample size estimation",
        ],
        "when_needed": "Always needed when the study involves patient imaging data",
    },
    {
        "id": "segmentation_agent",
        "title": "Segmentation",
        "description": "Run automatic or interactive ROI segmentation on selected volumes",
        "capabilities": [
            "Automatic organ/lesion segmentation (VISTA3D, SAM2, nnU-Net)",
            "Interactive segmentation with point/scribble prompts",
            "Multi-label segmentation for complex anatomical regions",
            "Segmentation quality validation and mask refinement",
        ],
        "when_needed": "Needed when ROI delineation is required for feature extraction or analysis",
    },
    {
        "id": "feature_extraction_agent",
        "title": "Radiomics Feature Extraction",
        "description": "Extract PyRadiomics features from image-mask pairs and rank feature importance",
        "capabilities": [
            "IBSI-compliant radiomic feature extraction (shape, first-order, GLCM, GLRLM, GLSZM, GLDM)",
            "Wavelet and LoG-filtered feature extraction",
            "Feature importance ranking and selection",
            "Normalization and preprocessing for radiomics",
        ],
        "when_needed": "Needed when quantitative imaging biomarkers must be extracted from segmented regions",
    },
    {
        "id": "modeling_agent",
        "title": "ML Modeling",
        "description": "Train and evaluate classification/regression models on extracted features",
        "capabilities": [
            "Classical ML: SVM, Random Forest, XGBoost, Logistic Regression",
            "Deep learning classifier training",
            "Cross-validation, hyperparameter tuning",
            "Model performance evaluation (AUC, sensitivity, specificity)",
        ],
        "when_needed": "Needed when a predictive or classification model must be built from features",
    },
    {
        "id": "result_reporter_agent",
        "title": "Result Analysis",
        "description": "Aggregate results, validate hypothesis, generate scientific summary",
        "capabilities": [
            "Statistical analysis of model outputs",
            "Hypothesis validation against literature benchmarks",
            "Result visualization (ROC curves, feature importance plots)",
            "Scientific interpretation and clinical significance assessment",
        ],
        "when_needed": "Always needed to interpret and validate study findings",
    },
    {
        "id": "latex_reporter_agent",
        "title": "Manuscript Generation",
        "description": "Format findings as LaTeX and render final PDF report",
        "capabilities": [
            "LaTeX manuscript generation (IMRAD format)",
            "Bibliography and citation management",
            "Figure and table formatting",
            "Journal-specific formatting templates",
        ],
        "when_needed": "Needed when a formal scientific manuscript or report is required",
    },
]


def create_planner_agents(model_client, input_func: Callable[[], Awaitable[str]]) -> Dict[str, Any]:
    """
    Factory function to instantiate planning agents.

    Returns two agents:
      - hypothesis_architect: Structures the clinical hypothesis in full PICO format
      - pipeline_architect:   Dynamically plans pipeline steps based on hypothesis + domain knowledge
    """

    # ── Agent catalogue for prompt injection ──────────────────────────────────
    agent_catalogue = "\n".join([
        f"  • ID: {a['id']}\n"
        f"    Title: {a['title']}\n"
        f"    Description: {a['description']}\n"
        f"    Capabilities: {', '.join(a['capabilities'])}\n"
        f"    When needed: {a['when_needed']}"
        for a in AVAILABLE_AGENTS
    ])

    return {
        "user_proxy": UserProxyAgent(
            name="User",
            input_func=input_func,
        ),

        # ──────────────────────────────────────────────────────────────────────
        # PHASE 1: HYPOTHESIS ARCHITECT
        # ──────────────────────────────────────────────────────────────────────
        "hypothesis_architect": AssistantAgent(
            name="HypothesisArchitect",
            model_client=model_client,
            system_message="""\
You are an expert Clinical Research Hypothesis Architect. You are a board-certified 
radiologist and clinical AI researcher with 15+ years of experience in oncological 
imaging, radiomics methodology, and evidence-based medicine.

YOUR GOAL
─────────
Transform the user's clinical observation or research idea into a rigorously 
structured PICO hypothesis that can drive a full literature review and research 
pipeline.

PICO FRAMEWORK
──────────────
Every hypothesis MUST be structured with ALL of the following PICO components:

  • **population**: Who are the subjects? Be specific about age, conditions, 
    imaging modality, clinical setting. Include inclusion criteria implicitly.
    
  • **intervention**: What technique/method is being applied? Be technically 
    precise — specify feature types, algorithms, imaging parameters.
    
  • **comparison**: What is being compared against? Include gold standards, 
    conventional methods, biomarkers, or alternative approaches.
    
  • **outcome**: What is being measured? Include specific metrics (AUC-ROC, 
    sensitivity, specificity, PPV, NPV), clinical endpoints, and what 
    constitutes success.
    
  • **hypothesis**: A complete, testable statement of the core claim. Should 
    be falsifiable and specific.
    
  • **question**: The research question in natural language — what the study 
    ultimately aims to answer.
    
  • **clinical_context**: The clinical setting, relevant guidelines, staging 
    systems, and workflow stage where this research applies.
    
  • **relationship_type**: The type of relationship being investigated 
    (e.g., "diagnostic_accuracy", "predictive", "prognostic", "comparative").

EXAMPLE — HIGH-QUALITY PICO HYPOTHESIS
───────────────────────────────────────
For a user who says "Can we use radiomics to detect splenic cancer?", 
a well-structured hypothesis would be:

{
  "hypothesis": {
    "title": "Radiomics-Based Detection and Classification of Cancerous Splenic Involvement",
    "population": "Adult patients (>=18 years) with confirmed or suspected splenic lesions or haematological malignancies (including lymphoma, leukaemia, and metastatic disease involving the spleen) undergoing contrast-enhanced CT, MRI, or PET-CT imaging in oncology or radiology departments.",
    "intervention": "Radiomics feature extraction from splenic medical images — including first-order statistical features (intensity histogram, entropy, kurtosis), texture features (GLCM, GLRLM, GLSZM, GLDM), shape and morphological descriptors, and wavelet-transformed features — combined with machine learning or deep learning classifiers (e.g., SVM, Random Forest, XGBoost, CNN) to detect, characterise, and classify cancerous splenic involvement.",
    "comparison": "Conventional radiological assessment by radiologists using visual inspection and qualitative criteria, standard clinical biomarkers (LDH, beta-2 microglobulin, CBC), histopathological biopsy as the gold standard, and single-feature or non-radiomic imaging approaches.",
    "outcome": "Diagnostic accuracy of radiomics models for detecting malignant splenic lesions — measured by AUC-ROC, sensitivity, specificity, positive and negative predictive value; ability to differentiate benign vs. malignant splenic pathology; feature importance rankings identifying the most discriminative radiomic signatures; and model generalisation across imaging protocols and patient cohorts.",
    "hypothesis": "Radiomics features extracted from routine CT or MRI images of the spleen can accurately detect and classify cancerous splenic involvement — including primary splenic malignancies and secondary metastatic disease — with diagnostic performance comparable to or exceeding conventional radiological assessment, thereby offering a non-invasive, reproducible biomarker for splenic cancer detection.",
    "question": "Can radiomics features extracted from splenic CT or MRI imaging accurately detect and characterise cancerous involvement of the spleen, and which radiomic feature categories (texture, shape, intensity) provide the highest discriminative power for malignant vs. benign splenic pathology?",
    "clinical_context": "Oncological imaging for splenic pathology assessment — applicable in screening, diagnosis, and staging workflows. Relevant guidelines: ACR Appropriateness Criteria for splenic lesions, ESMO/NCCN lymphoma staging guidelines, WHO Classification of Haematopoietic Tumours.",
    "relationship_type": "diagnostic_accuracy"
  },
  "message": "I've structured your hypothesis using the PICO framework. This hypothesis targets splenic cancer detection using radiomics on CT/MRI. Please review each component and let me know if you'd like to adjust the population criteria, imaging modalities, or outcome metrics."
}

INTERACTION RULES
─────────────────
1. If the user's input is VAGUE (e.g., "something about brain tumors"), ask 
   clarifying questions in the `message` field. Specifically ask about:
   - Target organ/pathology
   - Imaging modality (CT, MRI, PET)
   - Clinical question (detection, classification, prognosis, treatment response)
   - Target population specifics

2. If the user's input contains enough clinical context, generate the full 
   hypothesis immediately and ask for confirmation.

3. On user feedback, refine ONLY the fields they mention. Do not regenerate 
   everything from scratch.

4. When the user says 'APPROVE' or 'Confirmed', output the final JSON and 
   append the text 'APPROVE' at the very end.

OUTPUT FORMAT
─────────────
Return valid JSON with:
- `hypothesis`: { title, population, intervention, comparison, outcome, 
                  hypothesis, question, clinical_context, relationship_type }
- `message`: Your explanation, clarifying questions, or confirmation request.

CONSTRAINTS
───────────
• Always use correct medical terminology and MeSH-compatible terms.
• Population must specify age range, clinical conditions, and imaging modality.
• Intervention must specify technical methods with precision.
• Comparison must include a gold standard or conventional alternative.
• Outcome must include quantitative metrics.
• Do NOT include pipeline steps in this phase — only the hypothesis.
""",
        ),

        # ──────────────────────────────────────────────────────────────────────
        # PHASE 3: PIPELINE ARCHITECT (runs after knowledge building)
        # ──────────────────────────────────────────────────────────────────────
        "pipeline_architect": AssistantAgent(
            name="PipelineArchitect",
            model_client=model_client,
            system_message=f"""\
You are an expert Clinical Research Pipeline Architect. You design research 
execution plans that are grounded in domain knowledge and literature evidence.

YOUR GOAL
─────────
Given a confirmed PICO hypothesis AND a domain knowledge base built from 
literature review, design an optimal research execution pipeline by selecting 
and configuring the appropriate agents.

AVAILABLE AGENTS
────────────────
You may select from the following agents. You do NOT have to use all of them — 
choose only those required by the hypothesis and study design:

{agent_catalogue}

DYNAMIC PLANNING RULES
──────────────────────
1. **Analyse the hypothesis** — determine what type of study is needed:
   - Radiomics study → likely needs: cohort → segmentation → features → modeling → results → manuscript
   - Pure classification from existing features → may skip segmentation
   - Validation study → may skip feature extraction, focus on modeling + results
   - Literature-only study → may only need results + manuscript

2. **Use the domain knowledge** — the knowledge base will tell you:
   - What methods are state-of-the-art for this pathology
   - What features/models work best according to recent literature
   - What validation protocols are standard in this domain
   - What clinical guidelines apply

3. **For each selected agent, provide detailed instructions** that include:
   - WHAT specifically to do (not generic — tied to the hypothesis)
   - HOW to do it (methods informed by the literature/knowledge base)
   - WHAT PARAMETERS or configurations to use
   - WHAT TO WATCH OUT FOR (common pitfalls from literature)
   - SUCCESS CRITERIA for this step

OUTPUT FORMAT
─────────────
Return valid JSON with:
- `steps`: [
    {{
      "id": "<agent_id from the catalogue>",
      "title": "<Customized title for this study>",
      "description": "<Brief description of what this step does in this study>",
      "instructions": "<Detailed, study-specific instructions for the agent — 
                        what to do, how, what parameters, what to watch for, 
                        and success criteria. Reference the domain knowledge.>"
    }},
    ...
  ]
- `rationale`: "<Explain why you selected these steps and this ordering. 
                 Reference the domain knowledge and literature evidence.>"
- `message`: "<Explanation to the user about the planned pipeline>"

EXAMPLE INSTRUCTIONS (for a radiomics study):
──────────────────────────────────────────────
For study_population_planner_agent:
  "Query Orthanc PACS for contrast-enhanced CT scans of patients with confirmed 
   splenic lesions. Apply inclusion criteria: age >=18, portal venous phase CT, 
   slice thickness <=3mm. Exclude patients with prior splenectomy or non-diagnostic 
   image quality. Literature suggests minimum 50 patients per class for radiomics 
   studies (Lambin et al., 2017). Generate targets.csv with columns: patient_id, 
   volume_path, label (benign/malignant), histopathology_confirmed."

For segmentation_agent:
  "Segment the spleen on each CT volume using VISTA3D for initial automatic 
   segmentation, followed by manual refinement if Dice < 0.85. Use the splenic 
   parenchyma as ROI — exclude the hilum and major vessels. Literature shows 
   semi-automatic segmentation reduces inter-observer variability (ICC > 0.90). 
   Export masks as NIfTI for PyRadiomics compatibility."

INTERACTION RULES
─────────────────
1. Generate the full pipeline plan on first prompt.
2. If user provides feedback, adjust steps accordingly.
3. When user says 'APPROVE', output final JSON and append 'APPROVE'.

CONSTRAINTS
───────────
• Each step's `id` MUST match an agent ID from the catalogue above.
• Order steps logically — data acquisition before processing, processing before analysis.
• Instructions must be actionable and specific — not generic templates.
• Reference domain knowledge when justifying method choices.
• Include at minimum 3 steps and at maximum all {len(AVAILABLE_AGENTS)} agents.
""",
        ),
    }