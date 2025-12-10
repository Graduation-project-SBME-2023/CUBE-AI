"""
SegmentationModule – automates ROI segmentation.

The module receives an imaging protocol and raw volumes, then runs a
segmentation model (e.g., nnUNet, MONAI) to produce binary masks for the
structures of interest.
"""
from pathlib import Path
import sys
from typing import Dict, Any, Union
from autogen_ext.models.ollama import OllamaChatCompletionClient
import asyncio
from autogen_ext.tools.mcp import StdioServerParams, mcp_server_tools
from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_agentchat.ui import Console
from autogen_core import CancellationToken
from autogen_agentchat.teams import SelectorGroupChat
from autogen_agentchat.conditions import TextMentionTermination
from autogen_agentchat.base import TaskResult
import json
import logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

class SegmentationModule:
    """
    Implements the SEGMENTATION Team Pipeline using explicit agents and a SelectorGroupChat team.
    Handles automatic and interactive segmentation workflows.
    """
    def __init__(self, config_path: str = "config.json", model_name: str = "gpt-oss:120b-cloud"):
        self.config_path = config_path
        self.model_name = model_name
        self.model_client = None
        self.tools = None
        self.PlannerAgent = None
        self.ExecutionAgent = None
        self.UserFallbackAgent = None
        self.team = None

    async def initialize(self):
        self.model_client = OllamaChatCompletionClient(
            model=self.model_name,
            model_info={
                "vision": False,
                "function_calling": True,
                "json_output": True,
                "structured_output": True,
                "family": "unknown"
            },
            api_key="34d55bcebb014d6fa824c84eec5d3c61.fWWHg-syyiIDt02tVPQ3b5Of"
        )
        current_file = Path(__file__).resolve()
        backend_dir = current_file.parent.parent.parent  
        server_path = backend_dir / "mcp" / "server.py"   
        logger.info(f"➤ Starting MCP server at: {server_path}")     
        math_server = StdioServerParams(
            command="python", args=[str(server_path)]
        )
        try:
            self.tools = await mcp_server_tools(math_server)
            logger.info(f"Connected! Available tools count: {len(self.tools)}")
            
            # Log all available tool names
            for tool in self.tools:
                logger.info(f"  - Tool: {tool.name}")
            
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            raise 
        
        # Filter tools by name - mcp_server_tools returns a list, not a dict
        desired_tool_names = ['run_segmentation', 'get_model_info', 'check_having_mask']
        self.selected_tools = [tool for tool in self.tools if tool.name in desired_tool_names]
        
        logger.info(f"➤ Selected {len(self.selected_tools)} tools for SegmentationModule:")
        for tool in self.selected_tools:
            logger.info(f"  - {tool.name}")
        
        # Verify all desired tools were found
        found_names = {tool.name for tool in self.selected_tools}
        missing_tools = set(desired_tool_names) - found_names
        if missing_tools:
            logger.warning(f" Missing tools: {missing_tools}")

        self.PlannerAgent = AssistantAgent(
            name="PlannerAgent",
            system_message="""
            You are PlannerAgent, orchestrating segmentation:

            - Analyze the given hypothesis and dataset list to determine the target label(s) for segmentation.
            - Decide whether to run automatic segmentation or delegate to interactive user guidance based on:
                * Availability of automatic models for the target label.
                * Existence of previous segmentation masks.
            - Do NOT ask the user any questions yourself.
            - Only trigger UserFallbackAgent if human input, clarification, or confirmation is required.
            - Coordinate ExecutionAgent to run automatic segmentation models when available or found any previous existant masks .
            - Summarize the overall workflow and progress without asking the user directly.
            - Make decisions adaptively based on input data, model availability, and mask existence
            """,
            model_client=self.model_client,
            tools=self.selected_tools
        )

        self.ExecutionAgent = AssistantAgent(
            name="ExecutionAgent",
            system_message="""
            You are ExecutionAgent:
            - Check if a segmentation mask exists for a SeriesInstanceUID.
            - Check if an automatic model is available for the target label.
            - If no mask exists and a model is available, run automatic segmentation.
            - Never ask the user for confirmation.
            - If user confirmation is required (ambiguous case), hand over the task to UserFallbackAgent and stop.
            """,
            model_client=self.model_client,
            tools=self.selected_tools
        )

        self.UserFallbackAgent = AssistantAgent(
            name="UserFallbackAgent",
            system_message="""
            You are UserFallbackAgent:
            - You handle ALL human interactions.
            - Whenever confirmation or choice is needed, end your response with <ASK_USER>.
            - Wait for user input via UserProxy before continuing.
            - Guide interactive segmentation (points, boxes, labels) and provide navigation links navigate(`http://localhost:3000/monai-label?StudyInstanceUIDs=${uid}`).
            - Stop only when all masks are successfully created. 
            """,
            model_client=self.model_client,
            tools=self.selected_tools
        )
        
        self.UserProxy = UserProxyAgent(
            name="HumanUser",
            description="Proxy agent to relay human user input into the team",
            input_func=lambda prompt: input(prompt)
        )
        termination = TextMentionTermination("task_done")
        self.team = SelectorGroupChat(
            [self.PlannerAgent, self.ExecutionAgent, self.UserFallbackAgent, self.UserProxy],
            model_client=self.model_client,
            termination_condition=termination,
            # model_context=model_context,
            description="""SEGMENTATION Team Pipeline: 
        The PlannerAgent coordinates the overall segmentation workflow, breaking tasks into structured steps and determining which agent should act next. 
        The ExecutionAgent performs backend operations, including checking whether previous segmentation masks exist or whether an automatic model is available for the target label, and then running the  automatic segmentation model(if exist) over the relevant images. 
        The UserFallbackAgent interacts directly with the user during any part of the workflow that requires clarification, confirmation, parameter selection, or interactive segmentation guidance. 
        Whenever user confirmation or a choice is needed, control is handed to the UserFallbackAgent, and the system waits for the user’s response before continuing. 
        This pipeline proceeds step-by-step until all required tasks are completed and every segmentation mask has been successfully created, at which point the team terminates cleanly.."""
        )
        return True

    async def run(self, input_data: Union[str, Dict[str, Any]]):
        if isinstance(input_data, str):
            with open(input_data, "r") as f:
                input_data = json.load(f)
        instruction = input_data.get("hypothesis", "")
        datasetlist = input_data.get("datasetlist", "")
        task_prompt = f"""
        You are given a hypothesis: {instruction}
        You have a dataset list at {datasetlist} containing SeriesInstanceUIDs.
        Your task is to segment the target structure mentioned in the hypothesis across all volumes in the dataset list.
        """
        messages = []
        logger.info("=" * 80)
        logger.info("STARTING TEAM CONVERSATION")
        logger.info("=" * 80)

        async for message in self.team.run_stream(task=task_prompt):
                
            messages.append(message)

            source = getattr(message, "source", "")
            content = getattr(message, "content", "")

            # ----------------------------------------------------------------------
            # 1. FILTER: Only show Planner, Execution, Fallback
            # ----------------------------------------------------------------------
            if source in ("PlannerAgent","UserFallbackAgent"):
                printable = content
                if printable:
                    logger.info(f"\n[{source}] {printable}")

            # ----------------------------------------------------------------------
            # 2. UserFallbackAgent requires human input
            # ----------------------------------------------------------------------
            if source == "UserFallbackAgent" and content and "<ASK_USER>" in content:
                user_reply = input("\n[User Input Required] → ")
                await self.UserProxy.send(user_reply)  # send to team
                continue

        logger.info("=" * 80)
        logger.info("CONVERSATION COMPLETE")
        logger.info("=" * 80)

        return {"status": "finished"}


async def test_module():
    """Test function for the SegmentationModule"""
    try:
        logger.info("Starting SegmentationModule test...")
        
        # Initialize the module
        agent = SegmentationModule()
        await agent.initialize()
        
        # Run the module with your configuration file
        config_path = r"D:\GP_2026\Github\agenticsystem\OHIF-Standalone\Backend\mas\memory\workspace\configurations.json"
        logger.info(f"Running with config: {config_path}")
        
        result = await agent.run(config_path)
        
        logger.info("Test completed successfully!")
        logger.info(f"Result: {result}")
        
    except Exception as e:
        logger.error(f"Test failed with error: {e}", exc_info=True)


if __name__ == "__main__":
    # Run the test
    asyncio.run(test_module())


__all__ = ["SegmentationModule"]