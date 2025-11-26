from autogen_agentchat.agents import AssistantAgent
from pathlib import Path

class PopulationPlannerAgent(AssistantAgent):
    def __init__(self):
        super().__init__()
        current_dir = Path(__file__).resolve().parent
        self.config_path = current_dir.parent / "memory" / "workspace" / "state.json"
        