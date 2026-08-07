"""
Agent Factory Module for Clinical Research Pipeline

This module creates the planning agents responsible for hypothesis formulation
and pipeline orchestration in the clinical research automation system.

Architecture:
    Phase 1: Hypothesis Architect → Structures clinical question in PICO format
    Phase 2: Pipeline Architect    → Designs optimal execution pipeline
"""

from typing import Dict, Callable, Awaitable, Any
from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_ext.models.ollama import OllamaChatCompletionClient
from client_config import add_model_client_retries

from ..clinical_roi_policy import ROI_IDENTITY_POLICY


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL CLIENT FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

def create_model_client(
    api_key: str,
    model: str = "gpt-oss:120b-cloud"
) -> OllamaChatCompletionClient:
    """
    Create an Ollama chat completion client with standard configuration.

    Args:
        api_key: Authentication key for the model service.
        model: Model identifier string.

    Returns:
        Configured OllamaChatCompletionClient instance.
    """
    return add_model_client_retries(OllamaChatCompletionClient(
        model=model,
        model_info={
            "vision": False,
            "function_calling": True,
            "json_output": True,
        },
        api_key=api_key,
    ))


# ═══════════════════════════════════════════════════════════════════════════════
# AVAILABLE PIPELINE AGENTS CATALOGUE
# ═══════════════════════════════════════════════════════════════════════════════
# These are ALL the execution agents the system can schedule.
# The Pipeline Architect dynamically selects which ones are needed.

AVAILABLE_AGENTS = [
    {
        "id": "study_population_planner_agent",
        "title": "Cohort Discovery",
        "description": (
            "Query PACS, select study population, define inclusion/exclusion "
            "criteria, generate targets"
        ),
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
        "description": (
            "Run automatic or interactive ROI segmentation on selected volumes"
        ),
        "capabilities": [
            "Text-prompt organ/lesion segmentation via VoxTell (any anatomy or pathology)",
            "Interactive segmentation with point/scribble prompts",
            "Multi-label segmentation for complex anatomical regions",
            "Segmentation quality validation and mask refinement",
        ],
        "when_needed": (
            "Needed when ROI delineation is required for feature extraction "
            "or analysis"
        ),
    },
    {
        "id": "feature_extraction_agent",
        "title": "Radiomics Feature Extraction",
        "description": (
            "Extract PyRadiomics features from image-mask pairs and rank "
            "feature importance"
        ),
        "capabilities": [
            "IBSI-compliant radiomic feature extraction (shape, first-order, "
            "GLCM, GLRLM, GLSZM, GLDM)",
            "Wavelet and LoG-filtered feature extraction",
            "Feature importance ranking and selection",
            "Normalization and preprocessing for radiomics",
        ],
        "when_needed": (
            "Needed when quantitative imaging biomarkers must be extracted "
            "from segmented regions"
        ),
    },
    {
        "id": "modeling_agent",
        "title": "ML Modeling",
        "description": (
            "Train and evaluate classification/regression models on extracted "
            "features"
        ),
        "capabilities": [
            "Classical ML: SVM, Random Forest, XGBoost, Logistic Regression",
            "Deep learning classifier training",
            "Cross-validation, hyperparameter tuning",
            "Model performance evaluation (AUC, sensitivity, specificity)",
        ],
        "when_needed": (
            "Needed when a predictive or classification model must be built "
            "from features"
        ),
    },
    {
        "id": "result_reporter_agent",
        "title": "Result Analysis",
        "description": (
            "Aggregate results, validate hypothesis, generate scientific summary"
        ),
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
        "description": (
            "Format findings as LaTeX and render final PDF report"
        ),
        "capabilities": [
            "LaTeX manuscript generation (IMRAD format)",
            "Bibliography and citation management",
            "Figure and table formatting",
            "Journal-specific formatting templates",
        ],
        "when_needed": (
            "Needed when a formal scientific manuscript or report is required"
        ),
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# PLANNING AGENTS FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

def create_planner_agents(
    model_client: Any,
    input_func: Callable[[], Awaitable[str]],
) -> Dict[str, Any]:
    """
    Factory function to instantiate planning agents.

    Creates two specialized agents that work in sequence:
      1. Hypothesis Architect — Structures the clinical hypothesis in full PICO format.
      2. Pipeline Architect — Dynamically plans pipeline steps based on hypothesis
         and domain knowledge.

    Args:
        model_client: The LLM client instance for agent communication.
        input_func: Async callable for user input collection.

    Returns:
        Dictionary containing:
            - "user_proxy": UserProxyAgent for human interaction.
            - "hypothesis_architect": AssistantAgent for PICO hypothesis formulation.
            - "pipeline_architect": AssistantAgent for pipeline design.
    """

    # Build formatted agent catalogue for prompt injection
    agent_catalogue = "\n".join([
        (
            f"  • ID: {a['id']}\n"
            f"    Title: {a['title']}\n"
            f"    Description: {a['description']}\n"
            f"    Capabilities: {', '.join(a['capabilities'])}\n"
            f"    When needed: {a['when_needed']}"
        )
        for a in AVAILABLE_AGENTS
    ])

    # ── Phase 1: Hypothesis Architect System Prompt ───────────────────────
    hypothesis_architect_prompt = """\
You are an expert Clinical Research Hypothesis Architect. You are a board-certified 
radiologist and clinical AI researcher with 15+ years of experience in oncological 
imaging, radiomics methodology, and evidence-based medicine.

AUTHORITATIVE ROI IDENTITY POLICY
─────────────────────────────────
{ROI_IDENTITY_POLICY}

The validation stage supplies a locked ROI interpretation. Preserve its scope
and labels explicitly in both `intervention` and `question`. Do not replace an
organ ROI with a lesion, add a lesion, or remove a requested target because of
disease words elsewhere in the PICO statement.

INTERACTION MODE
────────────────
You are now in a conversational mode. Your goal is to **gradually** build a
complete PICO hypothesis through dialogue. If the user's input is incomplete,
do not reject it; instead ask specific, actionable questions about missing
PICO components and the ROI (whole organ, lesion, or both). Provide examples
to guide the user, e.g., "For whole-spleen radiomics, say 'whole spleen'.
For lesion-based, say 'lesions'."
Only output a structured hypothesis when you have all necessary information. 
Do NOT ask the user to confirm it; the system will proceed automatically.

When the user does not specify the ROI for feature extraction, ask:
"Do you want to extract features from the whole organ (e.g., whole spleen),
from the lesion/pathology (e.g., splenic lesions), or both? For example,
if you're studying splenic lymphoma, you might want whole-spleen radiomics,
whereas for tumor characterization you might want lesion-based features."

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
For a user who says "Can whole-spleen CT radiomics classify healthy versus
cancerous spleens?",
a well-structured hypothesis would be:

{
  "hypothesis": {
    "title": "Whole-Spleen Radiomics for Malignant Involvement Classification",
    "population": "Adult patients (>=18 years) undergoing contrast-enhanced abdominal CT with healthy spleens or suspected malignant splenic involvement.",
    "intervention": "Radiomics feature extraction from the whole-spleen ROI, including first-order, texture, shape, and wavelet features, combined with machine-learning classification.",
    "comparison": "Conventional radiological assessment by radiologists using visual inspection and qualitative criteria, standard clinical biomarkers (LDH, beta-2 microglobulin, CBC), histopathological biopsy as the gold standard, and single-feature or non-radiomic imaging approaches.",
    "outcome": "Classification performance for healthy spleens versus spleens with malignant involvement, measured by AUC-ROC, sensitivity, specificity, positive and negative predictive value.",
    "hypothesis": "CT radiomic features extracted from the whole spleen improve classification of healthy spleens versus spleens with malignant involvement compared with standard clinical parameters.",
    "question": "Do CT radiomic features extracted from the whole-spleen ROI improve classification of healthy spleens versus spleens with malignant involvement compared with standard clinical parameters?",
    "clinical_context": "Oncological imaging for splenic pathology assessment — applicable in screening, diagnosis, and staging workflows. Relevant guidelines: ACR Appropriateness Criteria for splenic lesions, ESMO/NCCN lymphoma staging guidelines, WHO Classification of Haematopoietic Tumours.",
    "relationship_type": "diagnostic_accuracy"
  },
  "message": "I've structured your hypothesis using the PICO framework with one explicit whole-spleen ROI on contrast-enhanced CT. The pipeline will now begin execution."
}

INTERACTION RULES
─────────────────
1. If the user's input is VAGUE (e.g., "something about brain tumors"), ask 
   clarifying questions in the `message` field. Specifically ask about:
   - Feature-extraction ROI: whole organ, lesion/pathology, or both
   - Imaging modality (CT, MRI, PET)
   - Clinical question (detection, classification, prognosis, treatment response)
   - Target population specifics

2. If the user's input contains enough clinical context, generate the full 
   hypothesis immediately. Do not ask for confirmation; the system will proceed.

3. On user feedback, refine ONLY the fields they mention. Do not regenerate 
   everything from scratch.

4. If the user says 'assume' or asks you to make assumptions, automatically assume reasonable clinical defaults for any missing PICO components or ROI. Generate the full structured hypothesis and state clearly in the `message` field what assumptions you made. Do NOT ask for confirmation.

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
• CRITICAL JSON FORMATTING: If you include any LaTeX commands or paths in your 
  instructions, you MUST double-escape all backslashes (e.g., use \\\\includegraphics 
  instead of \\includegraphics) so the JSON remains perfectly valid. Do not use 
  unescaped backslashes.
• STRICT JSON ONLY: You must output ONLY valid JSON format without any markdown 
  wrappers, fences (e.g. no ```json), or conversational text before or after the JSON.
""".replace("{ROI_IDENTITY_POLICY}", ROI_IDENTITY_POLICY)

    # ── Phase 2: Pipeline Architect System Prompt ────────────────────────
    pipeline_architect_prompt = f"""\
You are an expert Clinical Research Pipeline Architect. You design research 
execution plans that are grounded in domain knowledge and literature evidence.

AUTHORITATIVE ROI IDENTITY POLICY
─────────────────────────────────
{ROI_IDENTITY_POLICY}

The prompt may include a locked ROI interpretation from hypothesis validation.
It is authoritative. Every segmentation and feature-extraction instruction must
preserve exactly those targets, without substitution or expansion.

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
  "Preserve the explicitly requested extraction ROI. For whole-spleen radiomics,
   use one canonical target and VoxTell prompt named `spleen` on each CT volume,
   including healthy and diseased cohorts; do not add `spleen lesion`. For lesion
   radiomics, use only the explicitly named splenic lesion as the canonical
   target. Export masks as NIfTI for PyRadiomics compatibility."

INTERACTION RULES
─────────────────
1. Generate the full pipeline plan on first prompt.
2. If user provides feedback, adjust steps accordingly, or answer their questions.
3. When user says 'APPROVE', output final JSON and append 'APPROVE'.

CONSTRAINTS
───────────
• Each step's `id` MUST match an agent ID from the catalogue above.
• Order steps logically — data acquisition before processing, processing before 
  analysis.
• Instructions must be actionable and specific — not generic templates.
• Reference domain knowledge when justifying method choices.
• VoxTell prompts must be compact canonical target labels such as `spleen`;
  never generate command-style prompts such as `segment spleen`.
• Include at minimum 3 steps and at maximum all {len(AVAILABLE_AGENTS)} agents.
• CRITICAL JSON FORMATTING: If you include any LaTeX commands or paths in your 
  instructions, you MUST double-escape all backslashes.
• STRICT JSON ONLY: You must output ONLY valid JSON format without any markdown 
  wrappers, fences (e.g. no ```json), or conversational text before or after the JSON.
"""

    # ── Agent Instantiation ──────────────────────────────────────────────
    return {
        "user_proxy": UserProxyAgent(
            name="User",
            input_func=input_func,
        ),
        "hypothesis_architect": AssistantAgent(
            name="HypothesisArchitect",
            model_client=model_client,
            system_message=hypothesis_architect_prompt,
        ),
        "pipeline_architect": AssistantAgent(
            name="PipelineArchitect",
            model_client=model_client,
            system_message=pipeline_architect_prompt,
        ),
    }
