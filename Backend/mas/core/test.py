import os
import json
from autogen import AssistantAgent, UserProxyAgent, config_list_from_json

# Load configuration
config_path = os.path.join(os.path.dirname(__file__), "config.json")
try:
    config_list = config_list_from_json(config_path)
except Exception as e:
    # Fallback to environment variable
    if "OPENAI_API_KEY" not in os.environ:
        raise ValueError(
            "Please either create a config.json file or set OPENAI_API_KEY environment variable"
        )
    config_list = [
        {
            "model": "gpt-3.5-turbo",
            "api_key": os.environ["OPENAI_API_KEY"]
        }
    ]

# Create our planner assistant
planner = AssistantAgent(
    name="planner",
    system_message="You are a clinical study planner specialized in designing research protocols.",
    llm_config={
        "config_list": config_list,
    }
    
)

# Create a user proxy that never requests human input
user = UserProxyAgent(
    name="user",
    human_input_mode="NEVER"
)

# Test basic planning capabilities
test_query = """A radiomics-derived imaging signature from CT imaging can predict high-risk patients with neuroblastoma."""

user.initiate_chat(
    planner,
    message=f"Design a study plan to investigate this hypothesis: {test_query}"
)
