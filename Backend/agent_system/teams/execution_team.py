import sys
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional


def setup_logging():
    root_dir = Path(__file__).parent.parent
    log_dir = os.path.join(root_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = os.path.join(log_dir, f"execution_{timestamp}.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file, mode="w", encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ],
        force=True,
    )

    logging.info(f"Logging initialized: {log_file}")
    return log_file


# setup_logging()

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(root_dir)

import asyncio
from autogen_agentchat.agents import AssistantAgent, MessageFilterAgent, MessageFilterConfig, PerSourceFilter
from autogen_agentchat.teams import DiGraphBuilder, GraphFlow
from autogen_agentchat.ui import Console

# Import your existing custom agent classes
from agents.execution_agents.population_agent import PopulationPlannerAgent
from agents.execution_agents.segmentation_agent import SegmentationAgent
from agents.execution_agents.feature_extraction_agent import FeatureExtractionAgent
from agents.execution_agents.modeling_agent import ModelingAgent
from agents.execution_agents.result_reporter_agent import ResultReporterAgent
from agents.execution_agents.reporter_agent import ReporterAgent

# Import task message models and context compiler
from models.plans import TaskMessage
from agents.planning_agents.context_compiler import build_enriched_pipeline_prompt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent registry — maps each planner agent_id to its async factory.
# Add new agents here when extending the catalogue; nothing else needs
# to change for them to be picked up by the dynamic builder.
# ---------------------------------------------------------------------------
AGENT_FACTORIES = {
    "study_population_planner_agent": PopulationPlannerAgent.create,
    "segmentation_agent":             SegmentationAgent.create,
    "feature_extraction_agent":       FeatureExtractionAgent.create,
    "modeling_agent":                 ModelingAgent.create,
    "result_reporter_agent":          ResultReporterAgent.create,
    "latex_reporter_agent":           ReporterAgent.create,
}

# Fallback order when the planner provides no task_messages.
DEFAULT_PIPELINE_ORDER: List[str] = [
    "study_population_planner_agent",
    "segmentation_agent",
    "feature_extraction_agent",
    "modeling_agent",
    "result_reporter_agent",
    "latex_reporter_agent",
]


class BasicPipelineBuilder(DiGraphBuilder):

    def __init__(self, task_messages: Optional[List[TaskMessage]] = None):
        super().__init__()
        self.task_messages = task_messages or []

    @classmethod
    async def create(
        cls,
        workspace_folder=None,
        task_messages: Optional[List[TaskMessage]] = None,
        model_client=None,
        session_id=None,
        start_agent_id: Optional[str] = None,
    ):
        setup_logging()

        # ── Step 1: Resolve the ordered list of agent IDs from the planner ───
        #
        # We iterate over task_messages (already ordered by the planner) and
        # keep only agent IDs that are registered in AGENT_FACTORIES.  This
        # guards against the LLM hallucinating unknown agent names while still
        # honouring any custom ordering the planner chose.
        planned_ids: List[str] = []
        if task_messages:
            for tm in task_messages:
                if tm.agent_id in AGENT_FACTORIES and tm.agent_id not in planned_ids:
                    planned_ids.append(tm.agent_id)

        if not planned_ids:
            # Planner produced no usable plan → run the complete default pipeline
            logger.warning(
                "No valid task_messages received from planner — "
                "falling back to the default full pipeline."
            )
            planned_ids = list(DEFAULT_PIPELINE_ORDER)

        if start_agent_id:
            if start_agent_id not in planned_ids:
                raise ValueError(
                    f"Cannot resume from {start_agent_id!r}: agent is not in the "
                    f"planned pipeline {planned_ids!r}"
                )
            planned_ids = planned_ids[planned_ids.index(start_agent_id):]
            task_messages = [
                tm for tm in (task_messages or []) if tm.agent_id in planned_ids
            ]

        logger.info(
            "Dynamic pipeline will run %d agent(s): %s",
            len(planned_ids),
            " → ".join(planned_ids),
        )

        # ── Step 2: Instantiate only the agents that are actually needed ──────
        #
        # Previously ALL six agents were initialised on every run.  Now we only
        # pay the startup cost (MCP server subprocess, tool listing, etc.) for
        # the agents the planner selected.  For a liver-segmentation hypothesis
        # this means feature_extraction_agent and modeling_agent are never
        # created at all.
        raw_agents: dict = {}
        for agent_id in planned_ids:
            factory = AGENT_FACTORIES[agent_id]
            logger.info("Initialising agent: %s", agent_id)
            raw_agents[agent_id] = await factory(
                model_client=model_client,
                workspace_folder=workspace_folder,
                session_id=session_id,
            )

        # ── Step 3: Wrap agents with dynamic MessageFilterAgent ───────────────
        #
        # Wrapping rules:
        #   - The FIRST agent receives the initial GraphFlow task prompt
        #     directly, so it needs no filter.
        #   - Every subsequent agent is wrapped so it sees ONLY the last
        #     message from the agent that immediately precedes it in the plan.
        #     The filter source is set to prev_id — the actual preceding agent's
        #     name in this run's sequence — not a hardcoded string.
        #
        # This replaces the old fixed filter wiring where result_reporter_agent
        # always waited for "modeling_agent" even when modeling was not in the
        # plan, causing the reporter to never activate on segmentation studies.
        wrapped: dict = {}  # agent_id → graph node (raw agent or wrapped)

        for i, agent_id in enumerate(planned_ids):
            raw = raw_agents[agent_id]

            if i == 0:
                # First node — receives the initial task; no filter needed
                wrapped[agent_id] = raw
            else:
                prev_id = planned_ids[i - 1]
                wrapped[agent_id] = MessageFilterAgent(
                    name=agent_id,
                    wrapped_agent=raw,
                    filter=MessageFilterConfig(per_source=[
                        PerSourceFilter(source=prev_id, position="last", count=1)
                    ]),
                )

        # ── Step 4: Register nodes and build edges ────────────────────────────
        builder = cls(task_messages=task_messages)
        node_list = list(wrapped.values())

        for node in node_list:
            builder.add_node(node)

        for i in range(len(node_list) - 1):
            builder.add_edge(node_list[i], node_list[i + 1])

        return builder

    def get_pipeline_prompt(self, fallback_hypothesis: str = "") -> str:
        """
        Build the pipeline task prompt from stored task messages.
        Falls back to the raw hypothesis string if no task messages are available.
        """
        if self.task_messages:
            return build_enriched_pipeline_prompt(self.task_messages)
        elif fallback_hypothesis:
            return fallback_hypothesis
        else:
            return "Run the pipeline."


async def main() -> None:
    """
    Local test entrypoint — runs the full default pipeline (all 6 agents).
    This function is intentionally kept as a standalone test and is NOT
    used by the production run path (app.py → session_service → run_pipeline).
    """
    population_agent = await PopulationPlannerAgent.create()
    seg_agent = await SegmentationAgent.create()
    feat_agent = await FeatureExtractionAgent.create()
    model_agent = await ModelingAgent.create()
    report_agent = await ResultReporterAgent.create()
    latex_agent = await ReporterAgent.create()

    filtered_seg_agent = MessageFilterAgent(
        name="segmentation_agent",
        wrapped_agent=seg_agent,
        filter=MessageFilterConfig(per_source=[
            PerSourceFilter(source="study_population_planner_agent", position="last", count=1)
        ])
    )

    filtered_feat_agent = MessageFilterAgent(
        name="feature_extraction_agent",
        wrapped_agent=feat_agent,
        filter=MessageFilterConfig(per_source=[
            PerSourceFilter(source="segmentation_agent", position="last", count=1)
        ])
    )

    filtered_model_agent = MessageFilterAgent(
        name="modeling_agent",
        wrapped_agent=model_agent,
        filter=MessageFilterConfig(per_source=[
            PerSourceFilter(source="feature_extraction_agent", position="last", count=1)
        ])
    )

    filtered_report_agent = MessageFilterAgent(
        name="result_reporter_agent",
        wrapped_agent=report_agent,
        filter=MessageFilterConfig(per_source=[
            PerSourceFilter(source="user", position="first", count=1),
            PerSourceFilter(source="modeling_agent", position="last", count=1)
        ])
    )

    filtered_latex_agent = MessageFilterAgent(
        name="latex_reporter_agent",
        wrapped_agent=latex_agent,
        filter=MessageFilterConfig(per_source=[
            PerSourceFilter(source="result_reporter_agent", position="last", count=1)
        ])
    )

    builder = DiGraphBuilder()
    builder.add_node(population_agent)
    builder.add_node(filtered_seg_agent)
    builder.add_node(filtered_feat_agent)
    builder.add_node(filtered_model_agent)
    builder.add_node(filtered_report_agent)
    builder.add_node(filtered_latex_agent)

    builder.add_edge(population_agent, filtered_seg_agent)
    builder.add_edge(filtered_seg_agent, filtered_feat_agent)
    builder.add_edge(filtered_feat_agent, filtered_model_agent)
    builder.add_edge(filtered_model_agent, filtered_report_agent)
    builder.add_edge(filtered_report_agent, filtered_latex_agent)

    flow = GraphFlow(
        participants=builder.get_participants(),
        graph=builder.build()
    )

    task_prompt = """
    run the pipeline, this is the hypothesis (The radiomics features can detect weather the spleen is cancerous is not.)
    please use all the volumes that are abdomen, abdpel, venous arterial, ABA
    """

    print("--- Starting GraphFlow Pipeline ---")
    await Console(flow.run_stream(task=task_prompt))


if __name__ == "__main__":
    asyncio.run(main())
