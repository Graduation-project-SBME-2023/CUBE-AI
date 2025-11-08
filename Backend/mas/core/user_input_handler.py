import json
import logging
import asyncio
from pathlib import Path
import re
from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.ollama import OllamaChatCompletionClient

# ============================== CONFIG ==============================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("FeedbackAgent")

# ============================== HELPERS ==============================

# ============================== HELPERS ==============================
def clean_markdown_json(text: str) -> str:
    """Remove ```json ... ``` wrappers from model responses."""
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```", "", text)
    text = re.sub(r"```$", "", text)
    return text


# ============================== FEEDBACK AGENT ==============================

class FeedbackAgent:
    """Handles dynamic discussion and user confirmation of the study plan."""

    def __init__(self):
        # Resolve path safely (Backend/mas/... → Backend/memory/workspace/state.json)
        current_dir = Path(__file__).resolve().parent
        json_path = current_dir.parent / "memory" / "workspace" / "state.json"

        if not json_path.exists():
            logger.warning(f"State file not found at {json_path}")
            raise FileNotFoundError(f"Missing state.json at {json_path}")

        # Load stored hypothesis and plan
        with open(json_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        self.hypothesis = config.get("hypothesis", "")
        self.study_plan = config.get("plan", "")

        # Model setup
        self.model_client = OllamaChatCompletionClient(
            model="gpt-oss:120b-cloud",
            model_info={
                "vision": False,
                "function_calling": True,
                "json_output": True,
            },
            api_key="cf8ed2bae88d4d659602bc340b4b2bcf.mEvRr0YQ54A_OKfTT2Z92rge",
        )

        # LLM agent specialized in feedback discussion
        self.user_feedback_agent = AssistantAgent(
            name="UserInteractionAgent",
            system_message=(
                "You are a helpful radiology and research expert that discusses and explains the generated study plan "
                "for a given hypothesis. If the user asks questions, respond clearly and concisely. "
                "Always end your message by asking for confirmation: "
                "'Would you like to confirm this plan, or modify anything else?'. "
                "If the user confirms, respond in JSON format with: "
                "{'status': 'finished', 'message': 'Plan confirmed by user.'}. "
                "Otherwise, keep the conversation in JSON as {'status': 'pending', 'message': '<your reply>'}."
            ),
            model_client=self.model_client,
        )

    async def chat(self):
        """Simulate a dynamic chat loop with the user."""
        logger.info("Starting feedback chat loop.")
        print("=== Chat Simulation Started ===\n")

        # Initial system message
        system_prompt = (
            f"Discuss this study plan with the user:\n\nHypothesis:\n{self.hypothesis}\n\nPlan:\n{json.dumps(self.study_plan, indent=2)}"
        )
        
        print(f"hypothesis {self.hypothesis}")

        # Run first turn
        result = await self.user_feedback_agent.run(task=system_prompt)
        result_text = clean_markdown_json(result.messages[-1].content)

        try:
            response = json.loads(result_text)
        except json.JSONDecodeError:
            response = {"status": "pending", "message": result_text}


        print(json.dumps(response, indent=4))
        return response


import json
import logging
import re
import asyncio
from pathlib import Path
from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.ollama import OllamaChatCompletionClient

# ============================== LOGGING ==============================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("OutputFormattingAgent")



# ============================== OUTPUT FORMATTING AGENT ==============================

class OutputFormattingAgent:
    """
    This agent reformats messages/text/JSON objects into a user-friendly markdown,
    generates a short summary, and attaches the current application phase.

    The output is a JSON object with fields:
    {
        "status": "success" | "failed",
        "message": "Formatted successfully or error message",
        "markdown": "Markdown representation for UI display",
        "summary": "Brief summary of the content",
        "phase": "<phase name>"
    }
    """

    def __init__(self, phase: str = "unspecified"):
        self.phase = phase

        # Initialize LLM client
        self.model_client = OllamaChatCompletionClient(
            model="gpt-oss:120b-cloud",
            model_info={
                "vision": False,
                "function_calling": True,
                "json_output": True,
            },
            api_key="cf8ed2bae88d4d659602bc340b4b2bcf.mEvRr0YQ54A_OKfTT2Z92rge",
        )

        # Initialize specialized assistant
        self.formatter_agent = AssistantAgent(
            name="OutputFormatter",
            system_message=(
                "You are an output formatting assistant. "
                "Your task is to take any message, text, or JSON object, and reformat it "
                "into a user-friendly markdown output suitable for frontend display. "
                "Also, generate a concise summary of the content. "
                "Always respond strictly in JSON format:\n\n"
                "{\n"
                '  "status": "success",\n'
                '  "message": "Formatted successfully",\n'
                '  "markdown": "<the markdown representation>",\n'
                '  "summary": "<short summary>",\n'
                '  "phase": "<application phase>"\n'
                "}\n\n"
                "If formatting fails, return:\n"
                "{ 'status': 'failed', 'message': 'Error explanation' }"
            ),
            model_client=self.model_client,
        )

    async def format_output(self, content: str) -> dict:
        """
        Process and reformat the given content using the model.
        """
        logger.info(f"Formatting output for phase '{self.phase}'.")

        try:
            task = f"Reformat this content into markdown and summary:\n\n{content}"
            result = await self.formatter_agent.run(task=task)

            result_text = clean_markdown_json(result.messages[-1].content)
            parsed = json.loads(result_text)
            
            print(" --------------------- \n",parsed)

            if "status" not in parsed:
                parsed["status"] = "success"
            parsed["phase"] = self.phase

            logger.info("Output formatted successfully.")
            return parsed

        except json.JSONDecodeError as e:
            logger.error(f"JSON decoding failed: {e}")
            return {
                "status": "failed",
                "message": f"Invalid JSON from model: {str(e)}",
                "markdown": "",
                "summary": "",
                "phase": self.phase,
            }

        except Exception as e:
            logger.error(f"Formatting error: {e}")
            return {
                "status": "failed",
                "message": str(e),
                "markdown": "",
                "summary": "",
                "phase": self.phase,
            }


# ============================== TEST ENTRY ==============================

async def main():
    """Test the FeedbackAgent with live model interaction."""
    logger.info("Starting FeedbackAgent test.")
    agent = FeedbackAgent()
    response = await agent.chat()
    print("\n✅ Final Response:")
    print(json.dumps(response, indent=4))

if __name__ == "__main__":
    asyncio.run(main())
