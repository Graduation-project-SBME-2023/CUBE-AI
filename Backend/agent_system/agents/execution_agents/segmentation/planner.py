import json
import logging

from autogen_agentchat.agents import AssistantAgent

logger = logging.getLogger(__name__)


def create_planner_agent(model_client, discovery_tools):

    planner = AssistantAgent(

        name="PlannerAgent",
        model_client= model_client,
        tools= discovery_tools,        
        system_message = """
        You are PlannerAgent.
        Your responsibilities:
        1. Infer target anatomical label(s) from the instruction.
        2. Use the tool to discover available segmentation models.
        3. Analyze the returned models and their metrics (e.g., Dice scores).
        4. Decide whether segmentation should be automatic or interactive based on available models .

        After calling the tool and receiving the models information, you MUST analyze the results and output ONLY a JSON object (no other text) that STRICTLY follows this schema:

        {
            "target_labels": ["label1", "label2"],
            "chosen_path": "automatic",
            "selected_models": ["model1", "model2"]
        }

        Rules:
        - target_labels: Extract anatomical structures from the instruction (e.g., "spleen", "liver")
        - chosen_path: "automatic" if automatic models exist otherwise "interactive"
        - selected_models: List of selected models (automatic or interactive)
        - Output ONLY valid JSON, no explanations, no markdown
        - You MUST NOT output raw tool results. You must transform them into the required schema
        """ ,     
        # reflect_on_tool_use=True,
        max_tool_iterations=2
    )

    return planner


async def run_planner(agent, instruction):
    task = f"""
        Analyze this instruction:

        {instruction}

        Steps:
        1. Discover available segmentation models using the tool.
        2. Identify target anatomical label(s) from the instruction.
        3. Categorize discovered models as automatic or interactive.
        4. Determine if automatic models exist for the target labels.
        5. If automatic models exist, select it as 'selected_models'.
        6. If no automatic models are suitable, set selected model as voxtell
        7. Output a JSON plan STRICTLY following this schema:

        {{
            "target_labels": ["label1", "label2"],
            "chosen_path": "automatic",
            "selected_models": ["model1", "model2"],
            "reason": "why you chose each model, as text"
        }}
        or 
        {{
            "target_labels": ["label1", "label2"],
            "chosen_path": "interactive",
            "selected_models": ["voxtell"],
            "reason": "why you didn't choose any of the automatic options"
        }}

        Rules:
        - chosen_path: "automatic" if automatic models exist and are suitable, otherwise "interactive"
        - selected_models: automatic model if available, or any of the available interactive models in the model bank
        - Return a valid JSON object following the schema. Do not include explanations.
        """

    result = await agent.run(task=task)
    for i, msg in enumerate(result.messages):
        logger.debug("Planner message [%s] type=%s content=%s", i, type(msg).__name__, msg)
    content = result.messages[-1].content

    try:
        return json.loads(content)
    except Exception:
        raise RuntimeError(f"Invalid JSON from PlannerAgent: {content}")
