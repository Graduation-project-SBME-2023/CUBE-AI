import json
from autogen_agentchat.agents import AssistantAgent


def create_planner_agent(model_client, discovery_tools):

    planner = AssistantAgent(

        name="PlannerAgent",
        model_client= model_client,
        tools= discovery_tools,        
        system_message="""
                   You are PlannerAgent.
                Your responsibilities:
                1. Infer target anatomical label(s) from the hypothesis.
                2. Use  tool to discover available segmentation models.
                3. Analyze the returned models 
                4. Decide whether segmentation should be automatic or interactive based on available models.
                After calling the tool and receiving the model information, you MUST analyze the results and output ONLY a JSON object (no other text) that STRICTLY follows this schema:

                {
                "target_labels": ["label1", "label2"],
                "chosen_path": "automatic",
                "automatic_models": ["model1", "model2"],
                "interactive_models": ["model3"],
                "fallback_path": "interactive",
                "exclusion_rule": "exclude_existing_masks_for_same_label"
                }

                Rules:
                - target_labels: Extract anatomical structures from the hypothesis (e.g., "spleen", "liver")
                - chosen_path: "automatic" if automatic models exist for the target labels, otherwise "interactive"
                - automatic_models: List models where type contains "automatic" and match target labels
                - interactive_models: List models where type contains "interactive"
                - fallback_path: "interactive" if automatic fails, "none" if not applicable
                - Output ONLY valid JSON, no explanations or markdown

                    """,

        reflect_on_tool_use=True
    )

    return planner


async def run_planner(agent, hypothesis):
    task = f"""
        Analyze this hypothesis:

        {hypothesis}

        Steps:
        1.  discover available models
        2. Identify the target anatomical label(s) from the hypothesis
        3. Categorize models as automatic or interactive
        4. Determine if automatic models exist for the target labels
        5. Output the JSON plan following the required schema
        Remember: Output ONLY the JSON object, nothing else..
        """

    result = await agent.run(task=task)
    return json.loads(result.messages[-1].content)