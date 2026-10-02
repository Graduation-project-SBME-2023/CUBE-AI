import sys
import os
from storage_paths import workspace
import logging

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, "..", ".."))
sys.path.append(root_dir)

import os

from autogen_agentchat.agents import AssistantAgent
from clients.ollamaClient import OllamaClient
from autogen_agentchat.ui import Console
from autogen_core import CancellationToken
from descriptions.models_descriptions import MODELING_AGENT_DESCRIPTION
from system_prompts.system_messages import MODELING_AGENT_SYSTEM_PROMPT
from autogen_ext.tools.mcp import mcp_server_tools, StdioServerParams
from tools.classification_tool import classification_modelling
from autogen_core.tools import FunctionTool
from services.timeout_policy import MCP_COMPUTE_READ_SECONDS

modelling_classification_tool = classification_modelling
logger = logging.getLogger(__name__)


class ModelingAgent(AssistantAgent):
    def __init__(self, name, model_client, tools, description, system_message, max_tool_iterations):
        super().__init__(name=name,
                         model_client=model_client,
                         tools=tools,
                         description=description,
                         system_message=system_message,
                         max_tool_iterations=max_tool_iterations)

    @classmethod
    async def create(cls, model_client=None, workspace_folder=None, mcp_server_path=None, session_id=None, node_name=None, run_id=None, node_id=None):
        model_client = OllamaClient() if not model_client else model_client
        if not mcp_server_path:
            mcp_server_path = os.path.abspath(
                os.path.join(current_dir, "..", "..", "..", "mcp", "server.py")
            )
        else:
            mcp_server_path = os.path.abspath(mcp_server_path)
        if not os.path.exists(mcp_server_path):
            raise FileNotFoundError(f"MCP server not found at: {mcp_server_path}")
        env = os.environ.copy()
        env.update({
            "WORKSPACE_DIR": workspace_folder,
            "SESSION_ID": session_id,
            "AGENT_ID": "modeling_agent",
            "RUN_ID": str(run_id or ""),
            "NODE_ID": str(node_id or "")
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
            raise RuntimeError(f"Failed to initialize MCP tools from {mcp_server_path}") from exc
        desired_mcp_tools = ['read_agent_task_context', 'pycaret_train_classifier', 'pycaret_run_inference', 'scratchpad_register_agent', 'scratchpad_write_note', 'scratchpad_raise_flag',
                             'scratchpad_write_summary', 'scratchpad_set_status', 'scratchpad_read_agent',
                             'write_agent_output', 'browse_workspace']
        
        final_mcp_tools = cls.get_desired_tools(mcp_tools, desired_mcp_tools)
        # modelling_classification_tool_wrapper = FunctionTool(
        #     func=classification_modelling,
        #     description="""
        #     Asynchronously executes the full Classification Modelling pipeline.
        #     this tool have the capability internally to get the results of the feature extraction and work on it
        #     just call it when you need to start the feature extraction
        #     Use this tool in scenarios such as:
        #     1. When you need to train multiple machine learning algorithms on a dataset and automatically select the best one.
        #     2. To generate a complete  model metrics, confusion matrices, and ROC curves.
        #     """,
        #     name="classification_modelling"
        # )

        # tools = [modelling_classification_tool_wrapper, *final_mcp_tools]
        tools = final_mcp_tools

        # The node name identifies THIS step's execution; it falls
        # back to the agent type for the standalone/dev entrypoints below.
        name = node_name or "modeling_agent"
        description = MODELING_AGENT_DESCRIPTION
        system_message = MODELING_AGENT_SYSTEM_PROMPT
        return cls(
            name=name,
            model_client=model_client,
            tools=tools,
            description=description,
            system_message=system_message,
            max_tool_iterations=20
        )

    @staticmethod
    def get_desired_tools(mcp_tools, desired_tools):
        return [tool for tool in mcp_tools if tool.name in desired_tools]

async def main(client=None):
    from pathlib import Path
    # Initialize the agent using the factory method
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    SERVER_PATH = os.path.join(BASE_DIR, '..', '..', '..', 'mcp', 'server.py')
    session_id = "dev-session"
    workspace_dir = str(workspace(session_id))

    agent = await ModelingAgent.create(workspace_folder=workspace_dir, mcp_server_path=SERVER_PATH, session_id=session_id, model_client=client)

    # Now you can use the agent normally
    # print(f"Agent initialized with {agent._tools} tools.")
    # print(agent._max_tool_iterations)
    # for tool in agent._tools:
    #     print(tool.name)
    await Console(
        agent.run_stream(task="run the classification modeling",
                         cancellation_token=CancellationToken())
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
