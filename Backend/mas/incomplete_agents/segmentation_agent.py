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
import json
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

class SegmentationModule:
    """
    Implements the SEGMENTATION Team Pipeline using explicit agents and a SelectorGroupChat team.
    Handles automatic and interactive segmentation workflows.
    """
    def __init__(self, config_path: str = "config.json", model_name: str = "qwen3-coder:480b-cloud"):
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
                "json_output": False,
                "structured_output": False,
                "family": "unknown"
            }
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
            You are PlannerAgent, the orchestrator of the segmentation workflow. 
            Your role is to understand the system, coordinate agents, and plan intelligently. 

            Capabilities you have and understand in this system:

            - You can parse hypotheses and dataset lists (SeriesInstanceUIDs).  
            - You can communicate with ExecutionAgent to check for:
                * Available automatic segmentation models.
                * Existing masks.
                * Whether the target label exists for the dataset.  
            - You can decide between two main workflow paths:
                1. **Automatic path**: leverage available models to segment volumes automatically, consulting the user when masks exist or decisions are needed.  
                2. **Interactive path**: delegate segmentation guidance to UserFallbackAgent to guide the human user.  
            - You can delegate tasks to UserFallbackAgent for interactive guidance, waiting for user confirmation when needed.  
            - You understand the difference between automatic and interactive paths and can adaptively choose which to follow based on model availability, target label existence, and user input.  
            - You can summarize outcomes and provide a high-level overview when all datasets are processed.  
            - You know the capabilities of:
                * **ExecutionAgent** – check mask existence, execute segmentation, provide model info.  
                * **UserFallbackAgent** – instruct users, guide interactive segmentation, wait for confirmation.  
          
            Your focus is on understanding system capabilities, reasoning about workflow choices, coordinating agents, and adapting plans dynamically without following rigid step-by-step instructions.
            """,
            model_client=self.model_client,
            tools=self.selected_tools
        )

        self.ExecutionAgent = AssistantAgent(
            name="ExecutionAgent",
            system_message="""
            Executes backend operations:
            - Finds automatic model for the label 
            - Checks if mask exists for a SeriesInstanceUID 
            - Runs automatic segmentation 
            """,
            model_client=self.model_client,
            tools=self.selected_tools
        )

        self.UserFallbackAgent = AssistantAgent(
            name="UserFallbackAgent",
            system_message="""
            Guides the user in interactive segmentation:
            - Explains available interactive models (e.g., SAM, nnInteractive) getted from model info descripiton
            - Instructs on using prompts (points, boxes)
            - Guides label creation
            - Provides navigation route link for each UID: navigate(`http://localhost:3000/monai-label?StudyInstanceUIDs=${uid}`)
            - Reminds instructions and waits for user completion
            - Whenever you need the human to perform something or confirm, end your response with:
                <ASK_USER>
                Do NOT continue until the human replies.
            - stop when all masks are created successfully.
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
            description="SEGMENTATION Team Pipeline: PlannerAgent orchestrates, ExecutionAgent executes backend tasks, UserFallbackAgent guides interactive segmentation , terminate when all tasks are done and all masks have been successifully created."
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
            if source in ("PlannerAgent", "ExecutionAgent", "UserFallbackAgent"):
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