import sys
import os
from storage_paths import workspace, ws_sub
import logging

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, "..", ".."))
sys.path.append(root_dir)
from autogen_agentchat.agents import AssistantAgent
from clients.ollamaClient import OllamaClient
from autogen_ext.tools.mcp import mcp_server_tools, StdioServerParams
from descriptions.models_descriptions import FEATURE_EXTRACTION_AGENT_DESCRIPTION
from system_prompts.system_messages import FEATURE_EXTRACTION_AGENT_SYSTEM_PROMPT

# from tools.feature_extraction_tools import extract_radiomic_features, analyze_feature_importance
from autogen_core.tools import FunctionTool
from services.resource_tools import guard_named_tools
from services.timeout_policy import MCP_COMPUTE_READ_SECONDS

logger = logging.getLogger(__name__)

TASK_CONTEXT_BOOTSTRAP = """=== TASK CONTEXT BOOTSTRAP (MANDATORY) ===
Before doing anything else, call read_agent_task_context exactly once.
Use the returned task_message.instructions and context_augmentation as the primary task specification.
If the tool returns status other than 'ok', raise a scratchpad flag and stop.
"""


class FeatureExtractionAgent(AssistantAgent):
    def __init__(
        self,
        name,
        model_client,
        tools,
        description,
        system_message,
        max_tool_iterations,
    ):
        super().__init__(
            name=name,
            model_client=model_client,
            tools=tools,
            description=description,
            system_message=system_message,
            max_tool_iterations=max_tool_iterations,
        )

    @classmethod
    async def create(
        cls,
        model_client=None,
        workspace_folder=None,
        mcp_server_path=None,
        session_id=None,
        max_tool_iterations=35,
    ):
        model_client = OllamaClient() if not model_client else model_client

        if not mcp_server_path:
            mcp_server_path = os.path.abspath(
                os.path.join(current_dir, "..", "..", "..", "mcp", "server.py")
            )
        else:
            mcp_server_path = os.path.abspath(mcp_server_path)
        if not os.path.exists(mcp_server_path):
            raise FileNotFoundError(f"MCP server not found at: {mcp_server_path}")
        if not workspace_folder:
            raise ValueError(
                "workspace_folder is required — pass the absolute path to the agent workspace directory."
            )
        if not session_id:
            raise ValueError(
                "session_id is required — pass the active pipeline session UUID."
            )
        env = os.environ.copy()
        env.update({
            "WORKSPACE_DIR": workspace_folder,
            "SESSION_ID": session_id,
            "AGENT_ID": "feature_extraction_agent",
        })
        mcp_server = StdioServerParams(
            command=sys.executable,
            args=[mcp_server_path],
            env=env,
            read_timeout_seconds=MCP_COMPUTE_READ_SECONDS,
        )
        try:
            mcp_tools = await mcp_server_tools(mcp_server)
        except Exception as exc:
            logger.exception("Failed to initialize MCP tools from %s", mcp_server_path)
            raise RuntimeError(
                f"Failed to initialize MCP tools from {mcp_server_path}"
            ) from exc
        desired_mcp_tools = [
            "read_agent_task_context",
            "radiomics_extract_features",
            "radiomics_analyze_importance",
            "scratchpad_register_agent",
            "scratchpad_write_note",
            "scratchpad_raise_flag",
            "scratchpad_write_summary",
            "scratchpad_set_status",
            "scratchpad_read_agent",
            "write_agent_output",
            "browse_workspace",
        ]
        final_mcp_tools = cls.get_desired_tools(mcp_tools, desired_mcp_tools)
        final_mcp_tools = guard_named_tools(
            final_mcp_tools,
            {
                "radiomics_extract_features": ("cpu_heavy",),
                "radiomics_analyze_importance": ("cpu_heavy",),
            },
            session_id=str(session_id),
            agent_id="feature_extraction_agent",
        )
        name = "feature_extraction_agent"
        # tools = [feature_extraction_tool_wrapper, feature_importance_tool_wrapper, *final_mcp_tools]
        tools = final_mcp_tools
        description = FEATURE_EXTRACTION_AGENT_DESCRIPTION
        system_message = (
            f"{TASK_CONTEXT_BOOTSTRAP}\n\n{FEATURE_EXTRACTION_AGENT_SYSTEM_PROMPT}"
        )
        return cls(
            name=name,
            model_client=model_client,
            tools=tools,
            description=description,
            system_message=system_message,
            max_tool_iterations=max_tool_iterations,
        )

    @staticmethod
    def get_desired_tools(mcp_tools, desired_tools):
        return [tool for tool in mcp_tools if tool.name in desired_tools]


async def main():
    from autogen_agentchat.ui import Console
    from autogen_core import CancellationToken

    # client = OllamaClient("qwen3-coder:480b-cloud")

    # Initialize the agent using the factory method
    # agent = await FeatureExtractionAgent.create(client)
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    SERVER_PATH = os.path.join(BASE_DIR, "..", "..", "..", "mcp", "server.py")
    workspace_dir = str(workspace("dev-session"))

    agent = await FeatureExtractionAgent.create(
        workspace_folder=workspace_dir,
        mcp_server_path=SERVER_PATH,
        session_id="dev-session",
    )

    # Now you can use the agent normally
    print(f"Agent initialized with {agent._tools} tools.")
    print(agent._max_tool_iterations)
    for tool in agent._tools:
        print(tool.name)
    await Console(
        agent.run_stream(
            task=f"""Perform analysis, and find out the most important radiomics features for determining the health of the spleen. These are the inputs you'll need:
            {{
                "hypothesis": "What are the most important features for determining the health of the spleen?",
                "image_dir": "{ws_sub('dev-session', 'volumes')}",
                "mask_dir": "{ws_sub('dev-session', 'masks')}",
                "target_column": "target",
                "subject_id_column": "id",
            }}
        """,
            cancellation_token=CancellationToken(),
        )
    )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
