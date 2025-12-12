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
from  autogen_agentchat.teams import MagenticOneGroupChat, SelectorGroupChat
from autogen_agentchat.conditions import TextMentionTermination
from autogen_agentchat.ui import Console


async def main() -> None:
    model_client = OllamaClient()

    reporter_agent = await ReporterAgent.create()
    result_agent = await ResultReporterAgent.create()
    modeling_agent = await ModelingAgent.create()
    termination = TextMentionTermination("task_done")
    team = SelectorGroupChat([modeling_agent, result_agent, reporter_agent], model_client=model_client, termination_condition=termination)
    await Console(team.run_stream(task="""you are part of a radiomcs research study, the results of the feature
                                extraction is given, use the modeling agent to do the modeling step,
                                write the report and then save it in latex,
                                take the exact output of each agent and pass it to the next agent"""))


asyncio.run(main())