import json
import logging

from autogen_agentchat.agents import AssistantAgent
from .medical_vocabulary import build_prompt_synonym_section

logger = logging.getLogger(__name__)


def create_planner_agent(model_client, discovery_tools):

    planner = AssistantAgent(

        name="PlannerAgent",
        model_client=model_client,
        tools=discovery_tools,
        system_message="""
        You are PlannerAgent — a medical image segmentation router.
        Your job: given a clinical instruction, discover all available models, find the best matching
        label using medically-informed semantic reasoning, and select the most appropriate model.

        Output ONLY a single valid JSON object (no other text):
        {
            "target_labels": ["exact_catalog_label"],
            "chosen_path": "automatic" | "interactive",
            "selected_models": ["model_name"],
            "reason": "concise explanation of label match and model selection"
        }

        ═══════════════ MATCHING RULES ═══════════════

        RULE 1 — DETERMINE INTENT: Healthy organ vs. Pathology
        First, decide what the user is asking for:
        - HEALTHY ORGAN: "segment the spleen", "delineate the liver", "measure the kidney"
          → Correct: pick the healthy organ label ("spleen", "liver", "kidney_left").
        - ORGAN + PATHOLOGY: "splenic lesion", "liver cancer", "kidney tumor", "lung nodule"
          → Correct: find a label that captures BOTH the organ AND the specific pathology.
          → FORBIDDEN: picking a healthy-organ-only label (e.g., "spleen" for "splenic lesion").

        RULE 2 — ORGAN-ANCHORED PATHOLOGY (Anti Whole-Body Bug)
        When a pathology is requested for a SPECIFIC organ, the matched label MUST be organ-specific.
        Generic whole-body labels like "lesion", "tumor", "carcinoma" with no organ qualifier segment
        findings EVERYWHERE in the body — they are clinically WRONG for organ-specific requests.
        - "splenic lesion" → find "spleen_lesion", "splenic_tumor". NEVER pick "lesion" (whole body).
        - "pancreatic cancer" → find "pancreas tumor". NEVER pick "tumor" (whole body).
        - "lung nodule" → find "lung nodule", "lung lesion". NEVER pick "lesion" (whole body).
        - "liver cancer" → find "hepatocellular carcinoma", "liver_tumor". NEVER pick "carcinoma" (whole body).

        EXCEPTION: Generic pathology labels ARE acceptable ONLY when the user's request is also generic
        with no specific organ mentioned (e.g., "find any tumor", "detect any lesion in the scan").

        RULE 3 — FUZZY LABEL MATCHING (Smart Search, Not String Matching)
        When searching the catalog, apply all of the following transformations to find matches:
        a) Word order: "left kidney" = "kidney left" = "kidney_left" = "left_kidney" — all are the same.
        b) Plural/Singular: "tumors"="tumor", "lesions"="lesion", "nodules"="nodule", "vessels"="vessel".
        c) Medical synonyms (auto-generated from the same vocabulary the code uses):
{build_prompt_synonym_section()}
        d) Separators: spaces, underscores, and hyphens are interchangeable ("left_kidney" = "left kidney").
        e) Laterality expansion: if no side specified, check both left and right versions.

        RULE 4 — MODEL SELECTION PRIORITY
        After finding the best matching label:
        - Prefer automatic models (VISTA, nnUNet) when they have a specific catalog label matching the request.
        - If no automatic model has an exact or close match → use VoxTell (interactive path).
        - If tied between two automatic models, prefer the one with the more specific label match.

        RULE 5 — VOXTELL ACCEPTS ANY MEDICALLY VALID TEXT PROMPT
        VoxTell is a text-prompt model. It can segment ANY anatomical label or pathology
        described in natural language, even if that label is NOT listed in its catalog.
        - If a label IS in the VoxTell catalog → prefer it as it has been validated.
        - If a label is NOT in the VoxTell catalog → VoxTell can still segment it via dynamic embedding.
        - NEVER reject a request because VoxTell's catalog doesn't list the label.
        - If no automatic model has the label, always route to VoxTell.
        """,
        max_tool_iterations=2
    )

    return planner


async def run_planner(agent, instruction):
    task = f"""
        Analyze this clinical instruction and find the best segmentation model:

        INSTRUCTION: {instruction}

        ═══════════════ FOLLOW THESE STEPS EXACTLY ═══════════════

        STEP 1 — Discover models:
        Call the tool to get ALL available segmentation models and their labels.

        STEP 2 — Understand the clinical intent:
        Answer:
        a) What is the TARGET ANATOMY? (e.g., liver, spleen, kidney, pancreas)
        b) Is there a PATHOLOGY? (cancer, tumor, lesion, nodule, carcinoma, malignancy, etc.)
        c) CRITICAL — Is this a HEALTHY organ or DISEASED?
           - "Segment the spleen" → HEALTHY → find label "spleen". Valid and common.
           - "Splenic lesion" → DISEASED → must find an organ-specific pathology label.

        STEP 3 — Search for matching labels:

        IF HEALTHY ORGAN (no disease/pathology mentioned):
          - Find the healthy organ label using anatomical synonyms if needed.
          - Apply fuzzy matching: word order, plurals, underscores vs spaces, left/right variants.
          - Collect all candidates across all models.

        IF PATHOLOGY on a SPECIFIC ORGAN:
          Priority 1 — Organ-specific pathology label in an automatic model:
            - "splenic lesion" → search for: "spleen_lesion", "splenic_tumor", "splenic_lesion".
            - "liver cancer" → search for: "hepatocellular carcinoma", "liver_tumor", "liver_cancer".
            - "pancreatic cancer" → search for: "pancreas tumor", "pancreas_tumor".
            - "lung nodule" → search for: "lung nodule", "lung_nodule", "lung lesion", "lung_lesion".
            - "kidney tumor" → search for: "kidney_tumor", "renal_tumor", "kidney_cancer".
            - Apply FUZZY MATCHING: "lung nodules" matches "lung nodule"; "kidney tumors" matches "kidney tumor".
            - Apply WORD ORDER: "tumor kidney" matches "kidney tumor"; "left kidney" matches "kidney_left".

          Priority 2 — Synonym expansion (still organ-specific):
            - Expand both the organ AND the pathology name using all known synonyms.
            - Apply these synonyms (same as what the code uses internally):
{build_prompt_synonym_section()}
            - Still organ-anchored — do NOT lose the organ specificity.

          Priority 3 — VoxTell with the best matching label:
            - If no automatic model has a specific organ-pathology label, route to VoxTell.
            - VoxTell accepts ANY label including organ-pathology combinations not in its catalog.
            - Use the most descriptive, organ-specific label as the text prompt.
            - FORBIDDEN fallbacks (they produce wrong results for organ-specific requests):
              * Generic "lesion" (segments whole body, not just the organ)
              * Generic "tumor" (segments whole body)
              * Generic "carcinoma" (segments whole body)
              * The healthy organ label (segments the organ, not the disease)

        STEP 4 — Select the model:
        - Prefer automatic models when they have a specific catalog label for the request.
        - Fall back to VoxTell (interactive path) when no automatic model has the label.
        - VoxTell is always a valid choice — never output manual or empty selected_models for reasonable requests.

        STEP 5 — Output final JSON only:

        {{
            "target_labels": ["the_exact_catalog_label_you_chose"],
            "chosen_path": "automatic",
            "selected_models": ["model_name"],
            "reason": "Instruction=X. Intent=[healthy organ/organ+pathology]. Best label=Y in model=Z. Chosen because [label specificity reasoning]."
        }}
        or
        {{
            "target_labels": ["the_exact_label_or_text_prompt"],
            "chosen_path": "interactive",
            "selected_models": ["voxtell"],
            "reason": "Instruction=X. No automatic model has an exact match. Using VoxTell with label Y as text prompt."
        }}

        FINAL REMINDERS:
        - Healthy organ requests are normal — use the organ label directly.
        - Generic labels (lesion, tumor, carcinoma) are forbidden for organ-specific pathology requests.
        - Fuzzy match: plurals, word order, synonyms all count as valid matches.
        - VoxTell can segment ANY label — never refuse a reasonable medical request.
        - Output ONLY valid JSON. Zero text outside the JSON.
        """

    result = await agent.run(task=task)
    for i, msg in enumerate(result.messages):
        logger.debug("Planner message [%s] type=%s content=%s", i, type(msg).__name__, msg)
    content = result.messages[-1].content

    try:
        return json.loads(content)
    except Exception:
        raise RuntimeError(f"Invalid JSON from PlannerAgent: {content}")
