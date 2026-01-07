import asyncio
import sys
import os
import json
import logging
from datetime import datetime
import re
import uuid
import aiofiles
from pathlib import Path
from typing import Callable, Awaitable, Any, List

from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.conditions import TextMentionTermination
from autogen_agentchat.base import TaskResult

from .models import PlannerState, ClinicalHypothesis, StudyPlan, StudyStep
from .planning_agents import create_planner_agents, create_model_client

# Type aliases
InputProvider = Callable[[], Awaitable[str]]
OutputCallback = Callable[[str, Any], Awaitable[None]]

logger = logging.getLogger(__name__)

class ClinicalPlanner:
    def __init__(
        self, 
        input_provider: InputProvider,
        output_callback: OutputCallback,
        api_key: str,
    ):
        self.input_provider = input_provider
        self.output_handler = output_callback
        root_dir = Path(__file__).parent.parent.parent
        log_dir = os.path.join(root_dir, "logs")
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file = os.path.join(log_dir, f"planner_{timestamp}.log")
        config_dir = os.path.join(root_dir, "workspace")
        self.log_path = Path(log_file)
        
        self.state_path = Path(config_dir) / "state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.client = create_model_client(api_key)
        self.agents = create_planner_agents(self.client, input_func=self._user_proxy_input_adapter)
        
        self.state = PlannerState()
        self._initialize_logging()

    async def _user_proxy_input_adapter(self, prompt: str = None, cancellation_token=None) -> str:
        return await self.input_provider()

    def _initialize_logging(self):
        file_handler = logging.FileHandler(self.log_path)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(message)s'))
        std_handler = logging.StreamHandler(sys.stdout)
        logger.addHandler(file_handler)
        logger.addHandler(std_handler)

    async def initialize(self):
        await self._load_state()

    async def _send(self, type_: str, data: Any):
        await self.output_handler(type_, data)

    def _clean_json(self, text: str) -> str:
        text = text.strip()
        text = text.replace("APPROVE", "") 
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"^```", "", text)
        text = re.sub(r"```$", "", text)
        return text.strip()

    async def _save_state(self):
        try:
            async with aiofiles.open(self.state_path, 'w') as f:
                await f.write(self.state.model_dump_json(indent=2))
        except Exception as e:
            logger.error(f"Save Error: {e}")

    async def _load_state(self):
        if not self.state_path.exists(): return
        try:
            logger.info(f"will read state from file {self.state_path}")
            async with aiofiles.open(self.state_path, 'r') as f:
                content = await f.read()
                if content.strip():
                    self.state = PlannerState.model_validate_json(content)
        except Exception as e:
            logger.error(f"Load Error: {e}")

    async def _run_team(self, task: str):
        """
        Runs the consolidated Architect Team.
        """
        await self._send("info", "Starting Clinical Architect Session...")
        
        termination = TextMentionTermination("APPROVE")
        team = RoundRobinGroupChat(
            [self.agents["architect"], self.agents["user_proxy"]], 
            termination_condition=termination
        )
        
        async for message in team.run_stream(task=task):
            if isinstance(message, TaskResult):
                logger.info(f"Team finished. Reason: {message.stop_reason}")
                break
            
            logger.info(f"[{message.source}]: {message.content[:50]}...")
            
            if message.source == "User":
                continue

            clean_content = self._clean_json(message.content)
            
            try:
                data = json.loads(clean_content)
                
                # 1. Handle Hypothesis
                if "hypothesis" in data:
                    hypo_data = data["hypothesis"]
                    if "id" not in hypo_data or not hypo_data["id"]: 
                        hypo_data["id"] = self.state.hypothesis.id or str(uuid.uuid4())
                    
                    self.state.hypothesis = ClinicalHypothesis(**hypo_data)
                    await self._send("hypothesis", self.state.hypothesis.model_dump())

                # 2. Handle Plan (Fixed Steps Customization)
                if "steps" in data:
                    steps_data = data["steps"]
                    # Map the JSON steps to StudyStep objects. 
                    # We use 'module' field to store the static ID to track it.
                    self.state.plan = StudyPlan(
                        hypothesis_id=self.state.hypothesis.id,
                        study_goal=self.state.hypothesis.title,
                        steps=[
                            StudyStep(
                                name=s.get("title", ""), 
                                description=s.get("description", ""), 
                                module=s.get("id", "") # Storing the fixed ID in 'module' field
                            ) for s in steps_data
                        ]
                    )
                    
                    # Format for UI (flat list of steps with IDs)
                    ui_steps = [
                        {
                            "id": s.module, # Use the fixed ID (e.g., 'segmentation_agent')
                            "name": s.name, 
                            "description": s.description,
                            "module": s.module
                        } 
                        for s in self.state.plan.steps
                    ]
                    await self._send("plan", {"steps": ui_steps})

                # 3. Message
                if "message" in data:
                    await self._send("msg", data["message"])

                await self._save_state()

            except json.JSONDecodeError:
                display_text = message.content.replace("APPROVE", "").strip()
                if display_text:
                    await self._send("msg", display_text)

    async def run(self, start_query: str = "Start"):
        await self.initialize()
        
        # Only run if not already confirmed
        if self.state.hypothesis.state != "confirmed":
            
            initial_task = start_query
            if start_query == "Start" and not self.state.hypothesis.title:
                await self._send("msg", "Please enter your clinical observation to generate a study plan.")
                initial_task = await self.input_provider()
            
            # Single combined run
            await self._run_team(
                task=f"User Request: {initial_task}. Define Hypothesis and Customize Workflow. Wait for 'APPROVE'."
            )
            
            # Finalize
            self.state.hypothesis.state = "confirmed"
            self.state.plan.state = "confirmed"
            await self._save_state()
            
            await self._send("msg", "Study Plan Confirmed.")
            await self._send("modules_matched", True) # Signal frontend that plan is final