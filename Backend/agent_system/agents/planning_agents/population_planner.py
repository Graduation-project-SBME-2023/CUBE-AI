"""
Population Planner Agent Module

This module defines the PopulationPlannerAgent, a specialized assistant agent
responsible for cohort discovery and study population planning in the clinical
research pipeline. It handles DICOM querying, patient selection, target generation,
and initial report creation.

Architecture:
    - Extends AssistantAgent from autogen_agentchat
    - Uses MCP (Model Context Protocol) server for tool execution
    - Provides factory pattern for async instantiation
"""

import sys
import os
import asyncio
import logging
import json
import importlib.util
from pathlib import Path

from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import SystemMessage
from autogen_agentchat.ui import Console
from autogen_core import CancellationToken
from autogen_ext.tools.mcp import mcp_server_tools, StdioServerParams
from services.timeout_policy import MCP_STANDARD_READ_SECONDS

from clients.ollamaClient import OllamaClient
from descriptions.models_descriptions import POPULATION_PLANNER_AGENT_DESCRIPTION
from system_prompts.system_messages import POPULATION_PLANNER_AGENT_SYSTEM_MESSAGE

# ═══════════════════════════════════════════════════════════════════════════════
# PATH CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, "..", ".."))
sys.path.append(root_dir)

logger = logging.getLogger("Population Planner")


# ═══════════════════════════════════════════════════════════════════════════════
# POPULATION PLANNER AGENT
# ═══════════════════════════════════════════════════════════════════════════════

class PopulationPlannerAgent(AssistantAgent):
    """
    Specialized agent for study population planning and cohort discovery.

    This agent is responsible for:
        - Querying PACS (Orthanc) for DICOM studies
        - Selecting patient cohorts with inclusion/exclusion criteria
        - Generating target labels (targets.csv) for downstream ML
        - Performing demographic analysis and sample size estimation
        - Generating initial LaTeX reports

    Attributes:
        _workspace_folder (str): Path to the workspace directory.
        _session_id (str): Unique session identifier for the current run.
        _tools (list): MCP tools available to this agent.
    """

    def __init__(
        self,
        name: str,
        model_client: any,
        tools: list,
        description: str,
        system_message: str,
        max_tool_iterations: int,
    ):
        """
        Initialize the PopulationPlannerAgent.

        Args:
            name: Unique identifier for this agent instance.
            model_client: LLM client for agent communication.
            tools: List of MCP tools available for use.
            description: Agent capability description for orchestration.
            system_message: System prompt defining agent behavior.
            max_tool_iterations: Maximum number of tool call iterations allowed.
        """
        super().__init__(
            name=name,
            model_client=model_client,
            tools=tools,
            description=description,
            system_message=system_message,
            max_tool_iterations=max_tool_iterations,
        )

    # ── Class Methods ─────────────────────────────────────────────────────

    @classmethod
    async def create(
        cls,
        model_client: any = None,
        workspace_folder: str = None,
        session_id: str = None,
    ) -> "PopulationPlannerAgent":
        """
        Factory method to asynchronously create and configure a PopulationPlannerAgent.

        This method:
            1. Initializes the model client (defaults to OllamaClient)
            2. Locates and configures the MCP server
            3. Registers required MCP tools
            4. Instantiates the agent with full configuration

        Args:
            model_client: Optional pre-configured LLM client. If None, creates
                          a new OllamaClient instance.
            workspace_folder: Path to the workspace directory for file I/O.
            session_id: Unique session identifier for tracking.

        Returns:
            Fully configured PopulationPlannerAgent instance.

        Raises:
            FileNotFoundError: If the MCP server script cannot be found.
            RuntimeError: If MCP tool initialization fails.
        """
        # ── Model Client Setup ──────────────────────────────────────────
        model_client = OllamaClient() if not model_client else model_client

        # ── MCP Server Path Resolution ──────────────────────────────────
        mcp_server_path = os.path.abspath(
            os.path.join(current_dir, "..", "..", "..", "mcp", "server.py")
        )
        if not os.path.exists(mcp_server_path):
            raise FileNotFoundError(
                f"MCP server not found at: {mcp_server_path}"
            )

        # ── Environment Configuration ───────────────────────────────────
        env = os.environ.copy()
        env.update({
            "WORKSPACE_DIR": workspace_folder,
            "SESSION_ID": session_id,
            "AGENT_ID": "study_population_planner_agent",
        })

        # ── MCP Server Parameters ───────────────────────────────────────
        mcp_server = StdioServerParams(
            command=sys.executable,
            args=[mcp_server_path],
            env=env,
            read_timeout_seconds=MCP_STANDARD_READ_SECONDS,
        )

        # ── MCP Tool Initialization ─────────────────────────────────────
        try:
            mcp_tools = await mcp_server_tools(mcp_server)
        except Exception as exc:
            logger.exception(
                "Failed to initialize MCP tools from %s", mcp_server_path
            )
            raise RuntimeError(
                f"Failed to initialize MCP tools from {mcp_server_path}"
            ) from exc

        # ── Tool Selection ──────────────────────────────────────────────
        desired_tools = [
            "generate_targets",
            "get_patient",
            "get_volume",
            "save_final_output",
            "scratchpad_register_agent",
            "scratchpad_raise_flag",
            "scratchpad_write_summary",
            "scratchpad_set_status",
            "write_agent_output",
            "generate_initial_latex",
        ]
        final_tools = cls.get_desired_tools(mcp_tools, desired_tools)

        logger.info(
            "Started population selection with available tools: %d",
            len(final_tools),
        )
        for tool in final_tools:
            logger.info("Tool selected: %s", tool.name)

        # ── Agent Instantiation ─────────────────────────────────────────
        name = "study_population_planner_agent"
        description = POPULATION_PLANNER_AGENT_DESCRIPTION
        system_message = POPULATION_PLANNER_AGENT_SYSTEM_MESSAGE

        instance = cls(
            name=name,
            model_client=model_client,
            tools=final_tools,
            description=description,
            system_message=system_message,
            max_tool_iterations=20,
        )

        # Expose workspace and session for helper methods
        instance._workspace_folder = workspace_folder
        instance._session_id = session_id

        return instance

    # ── Public Methods ───────────────────────────────────────────────────

    async def generate_initial_report(
        self, sections: str = "introduction,literature_review"
    ) -> str:
        """
        Generate and compile the initial LaTeX document using the planning MCP tool.

        This helper calls the internal MCP tool implementation directly so the
        planner can generate the initial report at confirmation time, bypassing
        the normal agent conversation flow.

        Args:
            sections: Comma-separated string of section names to generate.
                      Default is "introduction,literature_review".

        Returns:
            JSON string containing the tool execution result, with structure:
                {"status": "success", ...} or {"status": "error", "message": "..."}

        Raises:
            No exceptions are raised — errors are caught and returned as JSON.
        """
        try:
            # Dynamically load the MCP tool module from the mcp tools folder
            # to avoid import path issues in different runtimes.
            tool_path = os.path.abspath(
                os.path.join(
                    current_dir, "..", "..", "..", "mcp", "tools",
                    "planning", "generate_initial_latex.py",
                )
            )
            spec = importlib.util.spec_from_file_location(
                "generate_initial_latex_tool", tool_path
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            generate_initial = getattr(module, "generate_initial_latex")

            result = await generate_initial(sections)
            return result

        except Exception as e:
            logger.exception("Failed to generate initial report: %s", e)
            return json.dumps({"status": "error", "message": str(e)})

    # ── Static Methods ───────────────────────────────────────────────────

    @staticmethod
    def get_desired_tools(mcp_tools: list, desired_tools: list) -> list:
        """
        Filter MCP tools to only those specified in the desired_tools list.

        Args:
            mcp_tools: Complete list of available MCP tools.
            desired_tools: List of tool names to select.

        Returns:
            Filtered list containing only the desired MCP tools.
        """
        for tool in mcp_tools:
            logger.info("Tool available: %s", tool.name)
        return [tool for tool in mcp_tools if tool.name in desired_tools]


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT (for testing)
# ═══════════════════════════════════════════════════════════════════════════════

async def main():
    """
    Test harness for the PopulationPlannerAgent.

    Initializes the agent and runs a sample task to verify configuration.
    """
    agent = await PopulationPlannerAgent.create()

    print(f"Agent initialized with {len(agent._tools)} tools.")

    await Console(
        agent.run_stream(
            task=(
                "A radiomics-derived imaging signature from CT imaging can "
                "predict high-risk patients with neuroblastoma"
            ),
            cancellation_token=CancellationToken(),
        )
    )

