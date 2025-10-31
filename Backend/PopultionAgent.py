from autogen_ext.models.ollama import OllamaChatCompletionClient
import asyncio
from autogen_ext.tools.mcp import StdioServerParams, mcp_server_tools
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.ui import Console
from autogen_core import CancellationToken
from pydantic import BaseModel
from typing import List, Tuple, Union

class IncludedData(BaseModel):
    modality: Union[str, List[str]]
    gender: Union[str, List[str]]
    age: Tuple[int, int]
    patient_ids: Union[str, List[str]]
    study_uids: Union[str, List[str]]
    model_config = {
        "title": "IncludedData",
        "description": "use this model if you have found cases to use in your population"
    }
    
# class ImagingInstructions(BaseModel):
#     imaging_requirements: dict[str:str]
#     model_config = {
#         "title":"ImagingInstructions",
#         "description": " use this only if you do not found the right population"
#     }
    
    



async def main() -> None:
    # Create server params for the remote MCP service
    math_server = StdioServerParams(
        command="python", args=["Backend/mcp/server.py"]
    )
    tools = await mcp_server_tools(math_server)
# 
    # Create an agent that can use the translation tool
    model_client = OllamaChatCompletionClient(
        model="gpt-oss:120b-cloud",
        model_info={
            "vision": False,
            "function_calling": True,
            "json_output": True,
            "structured_output": True
        }
    )
    agent = AssistantAgent(
        name="radiologist",
        model_client=model_client,
        tools=tools,
        max_tool_iterations= 5,
        system_message="""You are the study population selector.you will be given a hypothesis and you will use 
        the tools provided to get all the information about the patients and studies and then 
        you need to select what is the population that will be used to continue the study., in the output you must follow the model provided 
        do not output any thing else.DO NOT respond with any output except ONLY a text following the model provided.""",
        output_content_type=IncludedData
    )

    # Let the agent translate some text
    await Console(
        agent.run_stream(task="A radiomics-derived imaging signature from CT imaging can predict high-risk patients with neuroblastoma",
                         cancellation_token=CancellationToken())
    )
    # print(tools)

if __name__ == "__main__":
    asyncio.run(main())