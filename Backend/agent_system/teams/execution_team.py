import sys
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from storage_paths import LOGS_DIR


def setup_logging():
    log_dir = LOGS_DIR / "agent-system"
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

from agents.run_bound_agent import RunBoundAgent
from services.run_context import PlannedRun

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

class BasicPipelineBuilder(DiGraphBuilder):
    """Builds the execution graph from the session's **approved runs**."""

    def __init__(self, runs: Optional[List[PlannedRun]] = None):
        super().__init__()
        self.runs: List[PlannedRun] = runs or []
        self.entry_run: Optional[PlannedRun] = None

    @classmethod
    async def create(
        cls,
        workspace_folder=None,
        runs: Optional[List[PlannedRun]] = None,
        model_client=None,
        session_id=None,
        start_step_id: Optional[str] = None,
        on_start=None,
        on_finish=None,
        on_failure=None,
    ):
        setup_logging()

        runs = list(runs or [])
        unknown = [run.agent_type for run in runs if run.agent_type not in AGENT_FACTORIES]
        if unknown:
            # Admission already rejects these (the Application API validates
            # `agent_type` against the mirrored list before approving), so
            # reaching here means the two lists drifted. Fail loudly; never
            # skip a step silently.
            raise ValueError(
                f"Approved plan names agent(s) with no implementation: {unknown!r}"
            )
        if not runs:
            raise ValueError(
                "Cannot build execution pipeline: the session has no approved "
                "agent runs. Approve the plan first (A21)."
            )

        if start_step_id:
            step_ids = [run.step_id for run in runs]
            if start_step_id not in step_ids:
                raise ValueError(
                    f"Cannot resume from step {start_step_id!r}: it is not in this "
                    f"session's approved plan"
                )
            runs = runs[step_ids.index(start_step_id):]

        logger.info(
            "Execution graph: %d step(s): %s",
            len(runs),
            " -> ".join(f"{run.node_name}({run.agent_run_id[:8]})" for run in runs),
        )

        raw_agents: dict[str, object] = {}
        for run in runs:
            factory = AGENT_FACTORIES[run.agent_type]
            logger.info("Initialising %s for step %s", run.agent_type, run.step_id)
            raw_agents[run.step_id] = await factory(
                model_client=model_client,
                workspace_folder=workspace_folder,
                session_id=session_id,
                node_name=run.node_name,
                agent_run_id=run.agent_run_id,
                step_id=run.step_id,
            )

        async def _noop(*_args, **_kwargs):
            return None

        nodes = []
        for index, run in enumerate(runs):
            inner = raw_agents[run.step_id]
            if index > 0:
                # Each step sees only the last message of the step before it —
                # by NODE name, so two steps sharing an agent type stay
                # distinct.
                inner = MessageFilterAgent(
                    name=run.node_name,
                    wrapped_agent=inner,
                    filter=MessageFilterConfig(per_source=[
                        PerSourceFilter(
                            source=runs[index - 1].node_name, position="last", count=1
                        )
                    ]),
                )
            nodes.append(
                RunBoundAgent(
                    name=run.node_name,
                    wrapped_agent=inner,
                    run=run,
                    on_start=on_start or _noop,
                    on_finish=on_finish or _noop,
                    on_failure=on_failure or _noop,
                )
            )

        builder = cls(runs=runs)
        builder.entry_run = runs[0]
        for node in nodes:
            builder.add_node(node)
        for index in range(len(nodes) - 1):
            builder.add_edge(nodes[index], nodes[index + 1])
        return builder

    def get_pipeline_prompt(self, fallback_hypothesis: str = "") -> str:
        """The task the graph starts with."""
        if self.entry_run is not None:
            task_message = self.entry_run.task_message
            if task_message.get("instructions"):
                return str(task_message["instructions"])
            description = str(self.entry_run.instructions.get("description") or "")
            if description:
                return description
        return fallback_hypothesis or "Run the pipeline."


async def main() -> None:
    """Local test entrypoint — runs the full default pipeline (all 6 agents). This function is intentionally kept as a standalone test and is NOT used by the production run path (app.py → session_service → run_pipeline)."""
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
    Run the pipeline for this hypothesis: In adult patients undergoing
    contrast-enhanced abdominal CT, do radiomic features extracted from the
    whole-spleen ROI improve classification of healthy spleens versus spleens
    with malignant involvement compared with standard clinical parameters?
    please use all the volumes that are abdomen, abdpel, venous arterial, ABA
    """

    print("--- Starting GraphFlow Pipeline ---")
    await Console(flow.run_stream(task=task_prompt))


if __name__ == "__main__":
    asyncio.run(main())
