from autogen_ext.models.ollama import OllamaChatCompletionClient
import asyncio
from autogen_ext.tools.mcp import StdioServerParams, mcp_server_tools
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.ui import Console
from autogen_core import CancellationToken


async def main() -> None:
    # Create server params for the remote MCP service
    math_server = StdioServerParams(
        command="python", args=["Backend/mcp/server.py"]
    )
    tools = await mcp_server_tools(math_server)
# 
    # Create an agent that can use the translation tool
    model_client = OllamaChatCompletionClient(
        model="qwen3-coder:480b-cloud",
        model_info={
            "vision": False,
            "function_calling": True,
            "json_output": False,
            "structured_output": False
        }
    )
    agent = AssistantAgent(
        name="radiologist",
        model_client=model_client,
        tools=tools,
        system_message="you are a radiologist assistant, put a plan how to use your tools then run them",
    )

    # Let the agent translate some text
    await Console(
        agent.run_stream(task="this is a 3d ct case, my hypothesis is that this case has a tumer in the liver", cancellation_token=CancellationToken())
    )
    print(tools)

if __name__ == "__main__":
    asyncio.run(main())