import os
from autogen import AssistantAgent, UserProxyAgent

# ✅ Set your Groq API key in environment variable:
# On Windows PowerShell: setx GROQ_API_KEY "your_key_here"
# On Linux/Mac: export GROQ_API_KEY="your_key_here"

# ---- 1. AutoGen LLM Configuration ----
llm_config = {
    "config_list": [
        {
            "model": "openai/gpt-oss-20b",               # Groq model name
            "api_key": "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", # Your Groq key
            "base_url": "https://api.groq.com/openai/v1",
            "tool_choice": "none",
                            # tells AutoGen to use OpenAI API format
        },
    ],
    "temperature": 0.7,
    "timeout": 300,
}

# ---- 2. Initialize the Agents ----
assistant = AssistantAgent(
    name="groq_assistant",
    llm_config=llm_config,
    code_execution_config={"use_docker": False},  # avoid docker runtime errors
)

user = UserProxyAgent(
    name="user",
    code_execution_config={"use_docker": False},
)

# ---- 3. Start Conversation ----
user.initiate_chat(
    assistant,
    message="Hello! Please confirm you're using Groq and tell me your model name.",
)
