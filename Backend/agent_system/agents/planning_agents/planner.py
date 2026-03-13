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

# Removed Team imports
from autogen_agentchat.base import TaskResult
from autogen_agentchat.messages import TextMessage

from services.session_service import SessionService
# from .models import PlannerState, ClinicalHypothesis, StudyPlan, StudyStep
from .planning_agents import create_planner_agents, create_model_client
from models.plans import *
from models.session import Session

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
        session: Session,
        session_service: SessionService,
    ):
        self.input_provider = input_provider
        self.output_handler = output_callback
        root_dir = Path(__file__).parent.parent.parent
        log_dir = os.path.join(root_dir, "logs")
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file = os.path.join(log_dir, f"planner_{timestamp}.log")
        config_dir = os.path.join(root_dir, "workspace_" + session.session_id)
        self.log_path = Path(log_file)
        
        self.state_path = Path(config_dir) / "state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.client = create_model_client(api_key)
        # We only need the agents creation, no team wrapper needed
        self.agents = create_planner_agents(self.client, input_func=self._user_proxy_input_adapter)
        
        self.state = PlannerState()
        self.session = session
        self.session_service = session_service
        self._initialize_logging()

    async def _user_proxy_input_adapter(self, prompt: str = None, cancellation_token=None) -> str:
        return await self.input_provider()

    def _initialize_logging(self):
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
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
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
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

    async def _process_agent_response(self, response_content: str):
        """Helper to parse JSON from agent and update state/frontend."""
        logger.info(f"[Architect]: {response_content[:100]}...")
        
        clean_content = self._clean_json(response_content)
        
        try:
            data = json.loads(clean_content)
            
            # 1. Handle Hypothesis
            # FIX: Check if data["hypothesis"] is actually a dictionary (not None)
            if "hypothesis" in data and isinstance(data["hypothesis"], dict):
                self.state.hypothesis = await self.session_service.handle_hypothesis_update(
                    self.session, data["hypothesis"]
                )

            # 2. Handle Plan (Fixed Steps Customization)
            # FIX: Check if data["steps"] is a list
            if "steps" in data and isinstance(data["steps"], list):
                self.state.plan = await self.session_service.handle_plan_update(
                    self.session, data["steps"]
                )

            # 3. Message to User
            if "message" in data and data["message"]:
                await self._send("msg", data["message"])
            
            await self._save_state()

        except json.JSONDecodeError:
            # Fallback for non-JSON chat
            display_text = response_content.replace("APPROVE", "").strip()
            if display_text:
                await self._send("msg", display_text)
                
                
        except json.JSONDecodeError:
            # Fallback for non-JSON chat
            display_text = response_content.replace("APPROVE", "").strip()
            if display_text:
                await self._send("msg", display_text)


    async def run(self, start_query: str = "Start"):
        print(f"Planner {start_query} state before run:", self.state)
        
        # Only run if not already confirmed
        if self.state.hypothesis.state != "confirmed":
            print("state not confirmed, architect engaged")
            architect = self.agents["architect"]

            # Initial Prompt
            if start_query and not self.state.hypothesis.title:
                user_input = start_query
            else:
                await self._send("msg", "Please enter your clinical observation to generate a study plan.")
                user_input = await self.input_provider()

            # --- Sequential Planning Loop ---
            # 1. First iteration: Generate initial hypothesis/plan
            prompt = f"User Request: {user_input}. Generate JSON with 'hypothesis' and 'steps' matching the fixed workflow."

            # Direct call to agent (no team)
            response = await architect.on_messages(
                [TextMessage(content=prompt, source="user")],
                cancellation_token=None
            )
            print(f"response from architect: {response.chat_message.content}")

            # Process the response (parse JSON, update UI)
            await self._process_agent_response(response.chat_message.content)

            # 2. Refinement Loop
            while True:
                # Wait for user feedback
                await self._send("msg", "Review the plan. Type 'APPROVE' to finish or enter changes.")
                user_feedback = await self.input_provider()

                if "APPROVE" in user_feedback.upper():
                    break

                # Feed feedback back to the agent
                refinement_prompt = f"User Feedback: {user_feedback}. Update the JSON 'hypothesis' and 'steps' accordingly, or answer their query on the chosen aspects of each step. Do not update or resend the json if you are just answering the query."

                # Pass previous history implicitly if agent is stateful,
                # or here we just rely on the agent's internal memory if using AutoGen agents directly.
                response = await architect.on_messages(
                    [TextMessage(content=refinement_prompt, source="user")],
                    cancellation_token=None
                )

                await self._process_agent_response(response.chat_message.content)

            # --- Finalize ---
            self.state.hypothesis.state = "confirmed"
            self.state.plan.state = "confirmed"
            await self.session_service.confirm_plan(self.session)
            await self._save_state()

            await self._send("modules_matched", True)
