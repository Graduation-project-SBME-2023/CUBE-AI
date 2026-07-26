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
        Your job: given a clinical instruction, determine the best anatomical label or
        pathology description and route the request to VoxTell for segmentation.

        VoxTell is a text-prompt segmentation model. It is the ONLY active automatic
        segmentation model in this pipeline. It can segment ANY anatomical label or
        pathology described in natural language.

        Output ONLY a single valid JSON object (no other text):
        {
            "target_labels": ["exact_or_descriptive_label"],
            "chosen_path": "interactive",
            "selected_models": ["voxtell"],
            "reason": "concise explanation of label choice"
        }

        ═══════════════ MATCHING RULES ═══════════════

        RULE 1 — DETERMINE INTENT: Healthy organ vs. Pathology
        First, decide what the user is asking for:
        - HEALTHY ORGAN: "segment the spleen", "delineate the liver", "measure the kidney"
          → Pick the healthy organ label (e.g. "spleen", "liver", "kidney_left").
        - ORGAN + PATHOLOGY: "splenic lesion", "liver cancer", "kidney tumor", "lung nodule"
          → Construct an organ-specific pathology label (e.g. "spleen lesion", "liver tumor").
          → FORBIDDEN: picking a healthy-organ-only label for a pathology request.

        RULE 2 — ORGAN-ANCHORED PATHOLOGY
        When a pathology is requested for a SPECIFIC organ, the label MUST be organ-specific.
        Generic labels like "lesion", "tumor", "carcinoma" with no organ qualifier are
        clinically WRONG for organ-specific requests.
        - "splenic lesion" → use "spleen lesion" or "splenic tumor". NEVER just "lesion".
        - "pancreatic cancer" → use "pancreas tumor". NEVER just "tumor".
        - "lung nodule" → use "lung nodule" or "lung lesion". NEVER just "lesion".
        - "liver cancer" → use "liver tumor" or "hepatocellular carcinoma". NEVER just "carcinoma".

        EXCEPTION: Generic pathology labels ARE acceptable ONLY when the user's request is
        also generic with no specific organ (e.g. "find any tumor", "detect any lesion").

        RULE 3 — FUZZY LABEL MATCHING
        Apply these transformations to find the best label:
        a) Word order: "left kidney" = "kidney left" = "kidney_left" — all are the same.
        b) Plural/Singular: "tumors"="tumor", "lesions"="lesion", "nodules"="nodule".
        c) Medical synonyms:
{build_prompt_synonym_section()}
        d) Separators: spaces, underscores, and hyphens are interchangeable.
        e) Laterality expansion: if no side specified, use the unqualified label.

        RULE 4 — VOXTELL ACCEPTS ANY MEDICALLY VALID TEXT PROMPT
        VoxTell can segment ANY anatomical label or pathology described in natural language,
        even if no catalog lists it explicitly.
        - Use the most descriptive, organ-specific label possible as the text prompt.
        - NEVER reject a request because no catalog entry exists.
        - Always produce a valid label — never leave target_labels empty.

        ROUTING SUMMARY
        - chosen_path is ALWAYS "interactive"
        - selected_models is ALWAYS ["voxtell"]
        - target_labels must be the best organ/pathology label derived from the instruction
        """,
        max_tool_iterations=10  # No hard cap on tool calls
    )

    return planner


async def run_planner(agent, instruction):
    task = f"""
        Analyze this clinical instruction and determine the best segmentation label for VoxTell:

        INSTRUCTION: {instruction}

        ═══════════════ FOLLOW THESE STEPS EXACTLY ═══════════════

        STEP 1 — Understand the clinical intent:
        Answer:
        a) What is the TARGET ANATOMY? (e.g., liver, spleen, kidney, pancreas)
        b) Is there a PATHOLOGY? (cancer, tumor, lesion, nodule, carcinoma, malignancy, etc.)
        c) CRITICAL — Is this a HEALTHY organ or DISEASED?
           - "Segment the spleen" → HEALTHY → label = "spleen".
           - "Splenic lesion" → DISEASED → label = "spleen lesion" (organ-specific pathology).

        STEP 2 — Construct the best label:

        IF HEALTHY ORGAN (no disease/pathology mentioned):
          - Use the healthy organ label with standard anatomical naming.
          - Apply fuzzy matching: word order, plurals, underscores vs spaces.

        IF PATHOLOGY on a SPECIFIC ORGAN:
          - Construct an organ-specific pathology label:
            "splenic lesion" → "spleen lesion"
            "liver cancer" → "liver tumor"
            "pancreatic cancer" → "pancreas tumor"
            "lung nodule" → "lung nodule"
            "kidney tumor" → "kidney tumor"
          - Apply FUZZY MATCHING: plurals, word order, synonyms all count.
          - FORBIDDEN labels (too generic for organ-specific requests):
            * "lesion" (segments whole body)
            * "tumor" (segments whole body)
            * "carcinoma" (segments whole body)
            * The healthy organ label alone

        STEP 3 — Output final JSON only:

        {{
            "target_labels": ["the_best_label_you_chose"],
            "chosen_path": "interactive",
            "selected_models": ["voxtell"],
            "reason": "Instruction=X. Intent=[healthy organ/organ+pathology]. Best label=Y. VoxTell will segment via text prompt."
        }}

        FINAL REMINDERS:
        - chosen_path MUST always be "interactive".
        - selected_models MUST always be ["voxtell"].
        - Healthy organ requests are normal — use the organ label directly.
        - Generic labels (lesion, tumor, carcinoma) are forbidden for organ-specific pathology requests.
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


