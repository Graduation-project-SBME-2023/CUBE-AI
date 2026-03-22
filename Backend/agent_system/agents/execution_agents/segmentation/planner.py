import json
from autogen_agentchat.agents import AssistantAgent


def create_planner_agent(model_client, discovery_tools):

    planner = AssistantAgent(

        name="PlannerAgent",
        model_client= model_client,
        tools= discovery_tools,        
        system_message = """
        You are PlannerAgent.
        Your responsibilities:
        1. Infer target anatomical label(s) from the hypothesis.
        2. Use the tool to discover available segmentation models.
        3. Analyze the returned models and their metrics (e.g., Dice scores).
        4. Decide whether segmentation should be automatic or interactive based on available models and their metrics.

        After calling the tool and receiving the model information, you MUST analyze the results and output ONLY a JSON object (no other text) that STRICTLY follows this schema:

        {
            "target_labels": ["label1", "label2"],
            "chosen_path": "automatic",
            "automatic_models": ["model1", "model2"],
            "interactive_models": ["model3"],
            "selected_model": "model1",
            "fallback_path": "interactive",
            "exclusion_rule": "exclude_existing_masks_for_same_label"
        }

        Rules:
        - target_labels: Extract anatomical structures from the hypothesis (e.g., "spleen", "liver")
        - chosen_path: "automatic" if automatic models exist and have the highest metrics for the target labels, otherwise "interactive"
        - automatic_models: List all automatic models matching target labels
        - interactive_models: List all interactive models matching target labels
        - selected_model: The automatic model with the highest metric/Dice score, or the interactive model if fallback is used
        - fallback_path: "interactive" if no automatic models are suitable, otherwise "none"
        - exclusion_rule: Always include rules to avoid overwriting existing masks for the same label
        - Output ONLY valid JSON, no explanations, no markdown
        """ ,     
        reflect_on_tool_use=True
    )

    return planner


async def run_planner(agent, hypothesis):
    task = f"""
        Analyze this hypothesis:

        {hypothesis}

        Steps:
        1. Discover available segmentation models using the tool.
        2. Identify target anatomical label(s) from the hypothesis.
        3. Categorize discovered models as automatic or interactive.
        4. Determine if automatic models exist for the target labels.
        5. If automatic models exist, select the one with the highest metric or Dice score as 'selected_model'.
        6. If no automatic models are suitable, set 'fallback_path' to 'interactive' and select the best interactive model as 'selected_model'.
        7. Output a JSON plan STRICTLY following this schema:

        {{
            "target_labels": ["label1", "label2"],
            "chosen_path": "automatic",
            "automatic_models": ["model1", "model2"],
            "interactive_models": ["model3"],
            "selected_model": "model1",
            "fallback_path": "interactive",
            "exclusion_rule": "exclude_existing_masks_for_same_label"
        }}

        Rules:
        - chosen_path: "automatic" if automatic models exist and are suitable, otherwise "interactive"
        - selected_model: Highest metric automatic model if available, else best interactive model
        - fallback_path: "interactive" if automatic models are unavailable, else "none"
        - Always exclude existing masks for the same label
        - Output ONLY valid JSON, no explanations, no markdown
        """

    result = await agent.run(task=task)
    return json.loads(result.messages[-1].content)