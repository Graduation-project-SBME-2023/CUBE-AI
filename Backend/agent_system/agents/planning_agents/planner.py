import sys
import os
import json
import logging
from datetime import datetime
import re
import uuid
import asyncio
import aiofiles
from pathlib import Path
from typing import Callable, Awaitable, Any, List

# Removed Team imports
from autogen_agentchat.base import TaskResult
from autogen_agentchat.messages import TextMessage
from .models import PlannerState, ClinicalHypothesis, StudyPlan, StudyStep, TaskMessage
from .planning_agents import create_planner_agents, create_model_client
from .context_compiler import compile_task_messages, build_enriched_pipeline_prompt
from agents.literature_review_agent.knowledge_builder import build_knowledge_base

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
        session_id: str,
    ):
        self.input_provider = input_provider
        self.output_handler = output_callback
        root_dir = Path(__file__).parent.parent.parent
        log_dir = os.path.join(root_dir, "logs")
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file = os.path.join(log_dir, f"planner_{timestamp}.log")
        config_dir = os.path.join(root_dir, "workspace_" + session_id)
        self.log_path = Path(log_file)
        
        self.state_path = Path(config_dir) / "state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.client = create_model_client(api_key)
        # We only need the agents creation, no team wrapper needed
        self.agents = create_planner_agents(self.client, input_func=self._user_proxy_input_adapter)
        
        self.state = PlannerState()
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
                hypo_data = data["hypothesis"]
                
                # Safe check for ID
                if "id" not in hypo_data or not hypo_data["id"]: 
                    hypo_data["id"] = self.state.hypothesis.id or str(uuid.uuid4())
                
                self.state.hypothesis = ClinicalHypothesis(**hypo_data)
                await self._send("hypothesis", self.state.hypothesis.model_dump())

            # 2. Handle Plan (Fixed Steps Customization)
            # FIX: Check if data["steps"] is a list
            if "steps" in data and isinstance(data["steps"], list):
                steps_data = data["steps"]
                self.state.plan = StudyPlan(
                    hypothesis_id=self.state.hypothesis.id,
                    study_goal=self.state.hypothesis.title,
                    steps=[
                        StudyStep(
                            name=s.get("title", ""), 
                            description=s.get("description", ""), 
                            module=s.get("id", "") 
                        ) for s in steps_data
                    ]
                )
                
                # Format for UI 
                ui_steps = [
                    {
                        "id": s.module, 
                        "name": s.name, 
                        "description": s.description,
                        "module": s.module
                    } 
                    for s in self.state.plan.steps
                ]
                await self._send("plan", {"steps": ui_steps})

            # 3. Message to User
            if "message" in data and data["message"]:
                await self._send("msg", data["message"])
            
            await self._save_state()

        except json.JSONDecodeError:
            # Fallback for non-JSON chat
            display_text = response_content.replace("APPROVE", "").strip()
            if display_text:
                await self._send("msg", display_text)


    def _extract_pico_from_hypothesis(self) -> dict:
        """
        Extract a PICO dict from the confirmed hypothesis.
        Maps ClinicalHypothesis fields → knowledge_builder PICO format.
        """
        hyp = self.state.hypothesis
        
        population = hyp.population
        if isinstance(population, dict):
            population = ", ".join(f"{k}: {v}" for k, v in population.items())
        
        intervention = hyp.intervention
        if isinstance(intervention, dict):
            intervention = ", ".join(f"{k}: {v}" for k, v in intervention.items())
        
        outcome = hyp.outcome
        if isinstance(outcome, dict):
            outcome = ", ".join(f"{k}: {v}" for k, v in outcome.items())
        
        return {
            "population": str(population) if population else "",
            "intervention": str(intervention) if intervention else "",
            "comparison": "",  # Not always specified in hypothesis
            "outcome": str(outcome) if outcome else "",
            "hypothesis": hyp.title or "",
        }

    async def _build_knowledge_and_compile(self) -> list:
        """
        Run the knowledge builder (synchronous, CPU-bound) in a thread pool,
        then compile task messages with the context compiler.
        
        Returns list of TaskMessage objects.
        """
        pico = self._extract_pico_from_hypothesis()
        logger.info(f"Building knowledge base with PICO: {pico}")
        
        # build_knowledge_base is synchronous + does HTTP calls → run in executor
        loop = asyncio.get_event_loop()
        knowledge_base, sources_guide = await loop.run_in_executor(
            None, build_knowledge_base, pico
        )
        
        logger.info(
            f"Knowledge base built: {len(knowledge_base):,} chars, "
            f"sources guide: {len(sources_guide):,} chars"
        )
        
        # Store in state for later use
        self.state.knowledge_base = knowledge_base
        self.state.sources_guide = sources_guide
        
        # Compile structured task messages
        task_messages = compile_task_messages(
            hypothesis=self.state.hypothesis,
            plan=self.state.plan,
            knowledge_base=knowledge_base,
            sources_guide=sources_guide,
        )
        
        return task_messages

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
            await self._save_state()

            await self._send("msg", "Study Plan Confirmed. Building domain knowledge base...")

            # ── PHASE: Literature-Driven Knowledge Building ──────────────
            try:
                task_messages = await self._build_knowledge_and_compile()
                self.state.task_messages = task_messages
                await self._save_state()

                await self._send("msg", f"Knowledge base built. {len(task_messages)} structured task messages generated.")
                
                # Send task message summaries to UI
                task_summaries = [
                    {
                        "task_id": tm.task_id,
                        "phase": tm.phase,
                        "agent_id": tm.agent_id,
                        "agent_title": tm.agent_title,
                        "has_knowledge": bool(tm.context_augmentation.knowledge_summary),
                        "has_literature": bool(tm.context_augmentation.literature_findings),
                        "has_methodology": bool(tm.context_augmentation.methodology_guidance),
                    }
                    for tm in task_messages
                ]
                await self._send("task_messages", task_summaries)
            except Exception as e:
                logger.error(f"Knowledge building failed (non-fatal): {e}", exc_info=True)
                await self._send("msg", f"Warning: Knowledge base construction failed ({e}). Pipeline will proceed without literature augmentation.")

            await self._send("modules_matched", True)
