import sys
import os
from collections.abc import AsyncGenerator, Sequence

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, "..", ".."))
sys.path.append(root_dir)

from pathlib import Path
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import BaseAgentEvent, BaseChatMessage, TextMessage
from clients.ollamaClient import OllamaClient
from autogen_core.models import SystemMessage
from autogen_agentchat.ui import Console
from autogen_core import CancellationToken
from autogen_ext.tools.mcp import mcp_server_tools, StdioServerParams
from clients.backend_client import BackendClient
from services.population_handoff import population_handoff_issues
from services.timeout_policy import MCP_STANDARD_READ_SECONDS
from descriptions.models_descriptions import POPULATION_PLANNER_AGENT_DESCRIPTION
from system_prompts.system_messages import POPULATION_PLANNER_AGENT_SYSTEM_MESSAGE, POPULATION_EXEC_SYSTEM_MESSAGE
import asyncio
import logging
import pandas as pd

logger = logging.getLogger()

TASK_CONTEXT_BOOTSTRAP = """=== TASK CONTEXT BOOTSTRAP (MANDATORY) ===
Before doing anything else, call read_agent_task_context exactly once.
Use the returned task_message.instructions and context_augmentation as the primary task specification.
If the tool returns status other than 'ok', raise a scratchpad flag and stop.
"""

class PopulationPlannerAgent(AssistantAgent):
    COMPLETION_RETRIES = 2

    def __init__(
        self,
        name,
        model_client,
        tools,
        description,
        system_message,
        max_tool_iterations,
        workspace_folder=None,
        session_id=None,
        run_id=None,
    ):
        super().__init__(name=name,
                        model_client=model_client, 
                        tools=tools, 
                        description=description, 
                        system_message=system_message,
                        max_tool_iterations=max_tool_iterations)
        self._workspace_folder = workspace_folder
        # The handoff is checked against THIS step's run.
        self._session_id = session_id
        self._run_id = run_id
        self._population_completion_retries = self.COMPLETION_RETRIES

    async def on_messages_stream(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> AsyncGenerator[BaseAgentEvent | BaseChatMessage | Response, None]:
        """Suppress premature model responses until the handoff is durable."""
        pending_messages = messages
        attempts = self._population_completion_retries + 1

        for attempt in range(attempts):
            final_response: Response | None = None
            async for event in super().on_messages_stream(
                pending_messages,
                cancellation_token,
            ):
                if isinstance(event, Response):
                    final_response = event
                else:
                    yield event

            if final_response is None:
                raise RuntimeError("Population planner did not produce a final response")

            # Production always binds a session and a run. Keep the class usable
            # by its legacy local harness, where no durable records are expected.
            if not self._session_id:
                yield final_response
                return

            issues = await population_handoff_issues(self._session_id, self._run_id)
            if not issues:
                yield final_response
                return

            if attempt == attempts - 1:
                raise RuntimeError(
                    "Population planner tried to finish before its mandatory "
                    "handoff was complete: " + "; ".join(issues)
                )

            logger.warning(
                "Population planner attempted an incomplete handoff; retrying "
                "mandatory steps (%s)",
                "; ".join(issues),
            )
            pending_messages = [
                TextMessage(
                    source="user",
                    content=(
                        "Your previous response attempted to finish the population stage, "
                        "but its mandatory durable handoff is incomplete:\n- "
                        + "\n- ".join(issues)
                        + "\nContinue from the current tool state; do not restart the task and do "
                        "not return a final response yet. Call every missing mandatory "
                        "tool in order: validate_population_anatomy using the exact "
                        "volumes_detail from the latest search (an empty list is valid only "
                        "as an intermediate result), "
                        "extract_cohort_labels once if available, generate_targets and wait "
                        "until the doctor has selected and labeled at least one volume, "
                        "scratchpad_write_summary, "
                        "scratchpad_set_status(status='done'), then write_agent_output with "
                        "status='completed'. Only finish after all of these are recorded."
                    ),
                )
            ]
    
    @classmethod
    async def create(cls, model_client = None, workspace_folder=None, session_id=None, node_name=None, run_id=None, node_id=None):
        model_client = OllamaClient() if not model_client else model_client
        mcp_server_path = os.path.abspath(
            os.path.join(current_dir, "..", "..", "..", "mcp", "server.py")
        )
        if not os.path.exists(mcp_server_path):
            raise FileNotFoundError(f"MCP server not found at: {mcp_server_path}")
        env = os.environ.copy()
        cfg = None
        try:
            from client_config import get_client_config
            cfg = get_client_config()
        except ImportError:
            pass

        base_url = (cfg.base_url if cfg else None) or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        model_name = (cfg.model if cfg else None) or os.environ.get("MODEL_NAME", "llama3.1")
        api_key = (cfg.api_key if cfg else None) or os.environ.get("MODEL_API_KEY", "ollama")

        env.update({
            "WORKSPACE_DIR": workspace_folder,
            "SESSION_ID": session_id,
            "AGENT_ID": "study_population_planner_agent",
            "RUN_ID": str(run_id or ""),
            "NODE_ID": str(node_id or ""),
            "MODEL_BASE_URL": base_url,
            "MODEL_NAME": model_name,
            "MODEL_API_KEY": api_key,
            "LLM_CONCURRENCY_LIMIT": os.environ.get("LLM_CONCURRENCY_LIMIT", "5")
        })
        mcp_server = StdioServerParams(
            command=sys.executable,
            args=[mcp_server_path],
            env=env,
            read_timeout_seconds=MCP_STANDARD_READ_SECONDS
            )
        try:
            mcp_tools = await mcp_server_tools(mcp_server)
        except Exception as exc:
            logger.exception("Failed to initialize MCP tools from %s", mcp_server_path)
            raise RuntimeError(f"Failed to initialize MCP tools from {mcp_server_path}") from exc
        desired_tools = [
            'get_unique_series_descriptions',
            'search_volumes_by_description',
            'validate_population_anatomy',
            'write_inclusion_criteria',
            'generate_targets',
            'read_agent_task_context',
            'scratchpad_register_agent',
            'scratchpad_raise_flag',
            'scratchpad_write_summary',
            'scratchpad_set_status',
            'write_agent_output'
        ]
        
        # Only add extract_cohort_labels if mode is automatic. Since current build
        # this flow the labelling mode is `cohorts.criteria.labelling_mode`, set by
        # the Population UI before the step starts — not `config.json`.
        if session_id:
            try:
                cohort = await BackendClient.get_population_cohort(session_id)
                # "automatic" is the default the Population UI shows when the
                # doctor has not chosen, and a cohort the step has not created
                # yet is exactly that case. An unreadable cohort is different:
                # it falls through to the except below and the LLM adjudicator
                # stays out of the tool list.
                labelling_mode = ((cohort or {}).get("criteria") or {}).get(
                    "labelling_mode", "automatic"
                )
                if labelling_mode == "automatic":
                    desired_tools.insert(3, 'extract_cohort_labels')
            except Exception as e:
                logger.error(f"Failed to read labelling_mode from the cohort: {e}")

        final_tools = cls.get_desired_tools(mcp_tools, desired_tools)
        logger.info(f"Started population selection with available tools: {len(final_tools)}")
        for tool in final_tools:
            logger.info(f"Tool selected: {tool.name}")
        # The node name identifies THIS step's execution; it falls
        # back to the agent type for the standalone/dev entrypoints below.
        name = node_name or "study_population_planner_agent"
        description = POPULATION_PLANNER_AGENT_DESCRIPTION
        system_message = f"{TASK_CONTEXT_BOOTSTRAP}\n\n{POPULATION_EXEC_SYSTEM_MESSAGE}"
        return cls(
            name = name,
            model_client = model_client,
            tools = final_tools,
            description = description,
            system_message = system_message,
            max_tool_iterations = 20,
            workspace_folder = workspace_folder,
            session_id = session_id,
            run_id = str(run_id) if run_id else None,
        )
    
    @staticmethod
    def get_desired_tools(mcp_tools, desired_tools):
        for tool in mcp_tools:
            logger.info(f"Tool available: {tool.name}")
        return [tool for tool in mcp_tools if tool.name in desired_tools]


import asyncio

async def main():
    # Initialize the agent using the factory method
    agent = await PopulationPlannerAgent.create()
    
    # Now you can use the agent normally
    print(f"Agent initialized with {len(agent._tools)} tools.")
    await Console(
        agent.run_stream(task=(
            "In adult patients undergoing contrast-enhanced abdominal CT, do "
            "radiomic features extracted from the whole-spleen ROI improve "
            "classification of healthy spleens versus spleens with malignant "
            "involvement compared with standard clinical parameters?"
        ),
                         cancellation_token=CancellationToken())
        )
if __name__ == "__main__":
    asyncio.run(main())
    # print(root_dir)
    # csv_path = os.path.join(root_dir, "workspace", "fake_data", "data.csv")
    # data = pd.read_csv(csv_path)
    
    # json_path = os.path.join(root_dir, "workspace", "populations", "populations", "population.json")
    # json_file = pd.read_json(json_path)
    
    # print(get_unique_descriptions(root_dir))
    # print(search_using_keywords(root_dir, ["MR"], []))
