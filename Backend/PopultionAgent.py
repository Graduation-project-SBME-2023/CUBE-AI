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
        max_tool_iterations= 5,
        system_message="""You are the study population selector.you will be given a hypothesis and you will use 
        the tools provided to get all the information about the patients and studies and then 
        you need to select what is the population that will be used to continue the study. if the data not available
        you should provide the imaging protocol needed and its specifications""",
    )

    # Let the agent translate some text
    await Console(
        agent.run_stream(task="A radiomics-derived imaging signature from CT imaging can predict high-risk patients with neuroblastoma",
                         cancellation_token=CancellationToken())
    )
    # print(tools)

if __name__ == "__main__":
    asyncio.run(main())