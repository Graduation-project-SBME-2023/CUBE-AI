import asyncio
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.append(root_dir)


from clients.ollamaClient import OllamaClient
from agents.execution_agents.reporter_agent import ReporterAgent
from agents.execution_agents.result_reporter_agent import ResultReporterAgent
from agents.execution_agents.modeling_agent import ModelingAgent
from agents.planning_agents.population_planner import PopulationPlannerAgent
from agents.execution_agents.feature_extraction_agent import FeatureExtractionAgent
from  autogen_agentchat.teams import MagenticOneGroupChat, SelectorGroupChat
from autogen_agentchat.conditions import TextMentionTermination
from autogen_agentchat.ui import Console


async def main() -> None:
    model_client = OllamaClient()
    reporter_agent = await ReporterAgent.create()
    result_agent = await ResultReporterAgent.create()
    modeling_agent = await ModelingAgent.create()
    feature_extraction_agent = await FeatureExtractionAgent.create()
    termination = TextMentionTermination("task_done")
    team = MagenticOneGroupChat([feature_extraction_agent, modeling_agent, result_agent, reporter_agent], model_client=model_client, termination_condition=termination)
    await Console(team.run_stream(task="""you are part of a radiomcs research study, use the feature 
                                extraction agent to perform the feature extraction
                                , use the modeling agent to do the modeling step,
                                write the report and then save it in latex,
                                "image_dir": "C:/sbme 26/GP/refactored project/OHIF-Standalone/Backend/mas/memory/workspace/labels_data",
                                "mask_dir": "C:/sbme 26/GP/refactored project/OHIF-Standalone/Backend/mas/memory/workspace/labels_data",
                                "target_csv": "C:/sbme 26/GP/refactored project/OHIF-Standalone/Backend/mas/memory/workspace/targets.csv",
                                "target_label_name": "target"
                                take the exact output of each agent and pass it to the next agent"""))


asyncio.run(main())