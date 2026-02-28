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

# Static definition of the workflow nodes as per requirement
STATIC_NODES = [
    {"id": "study_population_planner_agent", "default_title": "Cohort Discovery", "default_desc": "Query PACS, select study population, define inclusion/exclusion criteria, generate targets"},
    {"id": "segmentation_agent", "default_title": "Segmentation", "default_desc": "Run automatic or interactive ROI segmentation on selected volumes"},
    {"id": "feature_extraction_agent", "default_title": "Radiomics Feature Extraction", "default_desc": "Extract PyRadiomics features from image-mask pairs and rank feature importance"},
    {"id": "modeling_agent", "default_title": "ML Modeling", "default_desc": "Train and evaluate classification models on extracted features"},
    {"id": "result_reporter_agent", "default_title": "Result Analysis", "default_desc": "Aggregate results, validate hypothesis, generate scientific summary"},
    {"id": "latex_reporter_agent", "default_title": "Manuscript Generation", "default_desc": "Format findings as LaTeX and render final PDF report"},
]

def create_planner_agents(model_client, input_func: Callable[[], Awaitable[str]]) -> Dict[str, Any]:
    """
    Factory function to instantiate agents.
    Now consolidated into a single 'Architect' agent for hypothesis and static plan customization.
    """
    
    # Generate a string representation of the static nodes for the prompt
    nodes_context = "\n".join([
        f"- ID: {node['id']} (Default Title: '{node['default_title']}', Default Desc: '{node['default_desc']}')"
        for node in STATIC_NODES
    ])

    return {
        "user_proxy": UserProxyAgent(
            name="User",
            input_func=input_func,
        ),
        "architect": AssistantAgent(
            name="ClinicalArchitect",
            model_client=model_client,
            system_message=(
                "You are an expert Clinical Research Architect. "
                "Your goal is to define a study based on the user's clinical goal.\n\n"
                
                "**TASK 1: HYPOTHESIS**\n"
                "Extract a structured clinical hypothesis with fields: "
                "title, population, intervention, outcome, clinical_context, relationship_type.\n\n"
                
                "**TASK 2: PLAN CUSTOMIZATION**\n"
                "We use a FIXED workflow of agents. You cannot add or remove steps. "
                "You MUST customize the 'title' and 'description' of each step to specifically match the hypothesis.\n"
                f"**The Fixed Agent Workflow**:\n{nodes_context}\n\n"
                
                "**OUTPUT FORMAT**\n"
                "Return valid JSON with:\n"
                "- `hypothesis`: { ... fields ... }\n"
                "- `steps`: [ { 'id': '<MUST MATCH FIXED ID>', 'title': '<Customized Title>', 'description': '<Customized Desc>' }, ... ]\n"
                "- `message`: Explanation/question to user.\n\n"
                
                "**INTERACTION RULES**\n"
                "- If input is vague, ask clarifying questions in `message`.\n"
                "- If solid, ask for confirmation.\n"
                "- If user says 'APPROVE' or 'Confirmed', output the final JSON and append the text 'APPROVE' at the very end."
            )
        )
    }