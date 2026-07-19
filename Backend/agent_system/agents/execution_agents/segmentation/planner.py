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
        label using medically-informed semantic reasoning, and select the model with the highest Dice score.

        Output ONLY a single valid JSON object (no other text):
        {
            "target_labels": ["exact_catalog_label"],
            "chosen_path": "automatic" | "interactive" | "manual",
            "selected_models": ["model_name"],
            "reason": "concise explanation of label match and dice score comparison"
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
        - If NO organ-specific pathology label exists → UNSUPPORTED. Honest and correct.

        EXCEPTION: Generic pathology labels ARE acceptable ONLY when the user's request is also generic
        with no specific organ mentioned (e.g., "find any tumor", "detect any lesion in the scan").

        RULE 3 — FUZZY LABEL MATCHING (Smart Search, Not String Matching)
        When searching the catalog, apply all of the following transformations to find matches:
        a) Word order: "left kidney" = "kidney left" = "kidney_left" = "left_kidney" — all are the same.
        b) Plural/Singular: "tumors"="tumor", "lesions"="lesion", "nodules"="nodule", "vessels"="vessel".
        c) Medical synonyms (auto-generated from the same vocabulary the code uses):
{build_prompt_synonym_section()}
        d) Separators: spaces, underscores, and hyphens are interchangeable ("left_kidney" = "left kidney").
        e) Laterality expansion: if no side specified, check both left and right versions and pick the higher Dice.

        RULE 4 — DICE SCORE IS THE PRIMARY SELECTOR
        After collecting ALL semantically valid candidate labels across ALL models (automatic and interactive):
        - Compare their Dice scores numerically.
        - Select the model with the HIGHEST Dice score for the matched label.
        - If tied within 1%, prefer automatic over interactive.
        - Model type (automatic vs interactive) does NOT matter — only the Dice score decides.

        RULE 5 — VOXTELL ACCEPTS ANY MEDICALLY VALID TEXT PROMPT
        VoxTell is a text-prompt model. It can segment ANY anatomical label or pathology
        described in natural language, even if that label is NOT listed in its catalog.
        - If a label IS in the VoxTell catalog → use its known Dice score for comparison.
        - If a label is NOT in the VoxTell catalog → VoxTell can still try it with Dice=0 (unvalidated).
        - Dice=0 means: prefer an automatic model if it has a matching label with a real Dice score.
          But if no automatic model has a specific organ-pathology label, choose VoxTell with Dice=0
          rather than returning UNSUPPORTED — it is better to attempt than to refuse.
        - NEVER return UNSUPPORTED just because VoxTell's catalog doesn't list the label.

        RULE 6 — UNSUPPORTED IS ONLY CORRECT WHEN NO MODEL CAN HELP
        Return UNSUPPORTED only when:
          a) No automatic model has an organ-specific pathology label, AND
          b) The request is for an organ-specific pathology that is clinically nonsensical to route to VoxTell.
        For all reasonable medical requests, prefer VoxTell with Dice=0 over UNSUPPORTED.
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
        Call the tool to get ALL available segmentation models, their labels, and Dice scores.

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
          - Collect all candidates across all models, compare Dice scores, pick the highest.

        IF PATHOLOGY on a SPECIFIC ORGAN:
          Priority 1 — Organ-specific pathology label:
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

          Priority 3 — UNSUPPORTED:
            - Declare UNSUPPORTED if no organ-specific pathology label exists after exhaustive search.
            - FORBIDDEN fallbacks (they produce wrong results):
              * Generic "lesion" (segments whole body, not just the organ)
              * Generic "tumor" (segments whole body)
              * Generic "carcinoma" (segments whole body)
              * The healthy organ label (segments the organ, not the disease)

        STEP 4 — Compare Dice scores:
        List all valid candidates: [label, model_name, dice_score].
        Select the one with the HIGHEST dice score. If tied, prefer automatic.

        STEP 5 — Output final JSON only:

        {{
            "target_labels": ["the_exact_catalog_label_you_chose"],
            "chosen_path": "automatic",
            "selected_models": ["model_name"],
            "reason": "Instruction=X. Intent=[healthy organ/organ+pathology]. Best label=Y in model=Z with Dice=W. Compared: [other candidates with scores]. Chosen: highest Dice."
        }}
        or
        {{
            "target_labels": ["the_exact_catalog_label_you_chose"],
            "chosen_path": "interactive",
            "selected_models": ["voxtell"],
            "reason": "Instruction=X. Best label=Y in interactive model with Dice=W beats automatic Dice=V."
        }}
        or
        {{
            "target_labels": ["UNSUPPORTED"],
            "chosen_path": "manual",
            "selected_models": [],
            "reason": "No organ-specific pathology label found. Generic whole-body labels rejected to avoid incorrect segmentation."
        }}

        FINAL REMINDERS:
        - Dice score is the ONLY selection criterion. Not model type.
        - Healthy organ requests are normal — use the organ label directly.
        - Generic labels (lesion, tumor, carcinoma) are forbidden for organ-specific pathology requests.
        - Fuzzy match: plurals, word order, synonyms all count as valid matches.
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
