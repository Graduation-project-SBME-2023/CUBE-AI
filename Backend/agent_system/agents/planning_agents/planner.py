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
    """
    Three-phase clinical research planner:
      Phase 1 — Hypothesis Structuring (PICO format)
      Phase 2 — Literature Review & Knowledge Building
      Phase 3 — Dynamic Pipeline Planning (knowledge-informed)
    """

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
        """Extract JSON from LLM response, handling markdown code fences anywhere."""
        text = text.strip()
        text = text.replace("APPROVE", "")

        # Try to extract JSON from a ```json ... ``` code fence anywhere in the text
        fence_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence_match:
            return fence_match.group(1).strip()

        # Try plain ``` ... ``` code fence
        fence_match = re.search(r"```\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence_match:
            return fence_match.group(1).strip()

        # Strip leading/trailing code fences (original behavior)
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"^```", "", text)
        text = re.sub(r"```$", "", text)

        # Last resort: extract the outermost { ... } block
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            return brace_match.group(0).strip()

        return text.strip()

    async def _save_state(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        try:
            async with aiofiles.open(self.state_path, 'w', encoding='utf-8') as f:
                await f.write(self.state.model_dump_json(indent=2))
        except Exception as e:
            logger.error(f"Save Error: {e}")

    async def _load_state(self):
        if not self.state_path.exists(): return
        try:
            logger.info(f"will read state from file {self.state_path}")
            async with aiofiles.open(self.state_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                if content.strip():
                    self.state = PlannerState.model_validate_json(content)
        except Exception as e:
            logger.error(f"Load Error: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # RESPONSE PROCESSORS
    # ══════════════════════════════════════════════════════════════════════════

    async def _process_hypothesis_response(self, response_content: str):
        """Parse hypothesis JSON from the HypothesisArchitect and update state/UI."""
        logger.info(f"[HypothesisArchitect]: {response_content[:100]}...")
        
        clean_content = self._clean_json(response_content)
        
        try:
            data = json.loads(clean_content)
            
            if "hypothesis" in data and isinstance(data["hypothesis"], dict):
                hypo_data = data["hypothesis"]
                
                if "id" not in hypo_data or not hypo_data["id"]: 
                    hypo_data["id"] = self.state.hypothesis.id or str(uuid.uuid4())
                
                self.state.hypothesis = ClinicalHypothesis(**hypo_data)
                await self._send("hypothesis", self.state.hypothesis.model_dump())

            if "message" in data and data["message"]:
                await self._send("msg", data["message"])
            
            await self._save_state()

        except json.JSONDecodeError:
            display_text = response_content.replace("APPROVE", "").strip()
            if display_text:
                await self._send("msg", display_text)

    async def _process_pipeline_response(self, response_content: str):
        """Parse pipeline plan JSON from the PipelineArchitect and update state/UI."""
        logger.info(f"[PipelineArchitect]: {response_content[:100]}...")
        
        clean_content = self._clean_json(response_content)
        
        try:
            data = json.loads(clean_content)
            
            if "steps" in data and isinstance(data["steps"], list):
                steps_data = data["steps"]
                self.state.plan = StudyPlan(
                    hypothesis_id=self.state.hypothesis.id,
                    study_goal=self.state.hypothesis.title,
                    steps=[
                        StudyStep(
                            name=s.get("title", ""), 
                            description=s.get("description", ""),
                            module=s.get("id", ""),
                        ) for s in steps_data
                    ]
                )
                
                # Format for UI — include instructions summary
                ui_steps = [
                    {
                        "id": s.get("id", ""),
                        "name": s.get("title", ""),
                        "description": s.get("description", ""),
                        "module": s.get("id", ""),
                        "instructions": s.get("instructions", "")[:200] + "..." 
                            if len(s.get("instructions", "")) > 200 
                            else s.get("instructions", ""),
                    } 
                    for s in steps_data
                ]
                await self._send("plan", {"steps": ui_steps})

                # Store full instructions in task_messages for later use
                self._pipeline_instructions = {
                    s.get("id", ""): s.get("instructions", "") 
                    for s in steps_data
                }

            if "rationale" in data and data["rationale"]:
                await self._send("msg", f"**Pipeline Rationale:** {data['rationale']}")

            if "message" in data and data["message"]:
                await self._send("msg", data["message"])
            
            await self._save_state()

        except json.JSONDecodeError as e:
            logger.error(f"Pipeline JSON parse error: {e}")
            logger.error(f"Clean content (first 300 chars): {clean_content[:300]}")
            print(f"[DEBUG] Pipeline JSON parse error: {e}")
            print(f"[DEBUG] Clean content preview: {clean_content[:200]}...")
            display_text = response_content.replace("APPROVE", "").strip()
            if display_text:
                await self._send("msg", display_text)

    # ══════════════════════════════════════════════════════════════════════════
    # PICO EXTRACTION & KNOWLEDGE BUILDING
    # ══════════════════════════════════════════════════════════════════════════

    def _extract_pico_from_hypothesis(self) -> dict:
        """
        Extract a PICO dict from the confirmed hypothesis.
        Maps ClinicalHypothesis fields → knowledge_builder PICO format.
        """
        hyp = self.state.hypothesis
        
        def _to_str(val):
            if isinstance(val, dict):
                return ", ".join(f"{k}: {v}" for k, v in val.items())
            return str(val) if val else ""
        
        return {
            "population": _to_str(hyp.population),
            "intervention": _to_str(hyp.intervention),
            "comparison": _to_str(hyp.comparison),
            "outcome": _to_str(hyp.outcome),
            "hypothesis": hyp.hypothesis or hyp.title or "",
            "question": hyp.question or "",
        }

    async def _build_knowledge_base(self) -> tuple:
        """
        Run the knowledge builder (synchronous, CPU-bound) in a thread pool.
        Returns (knowledge_base, sources_guide) strings.
        """
        pico = self._extract_pico_from_hypothesis()
        logger.info(f"Building knowledge base with PICO: {pico}")
        
        loop = asyncio.get_event_loop()
        knowledge_base, sources_guide = await loop.run_in_executor(
            None, build_knowledge_base, pico
        )
        
        logger.info(
            f"Knowledge base built: {len(knowledge_base):,} chars, "
            f"sources guide: {len(sources_guide):,} chars"
        )
        
        self.state.knowledge_base = knowledge_base
        self.state.sources_guide = sources_guide
        await self._save_state()
        
        return knowledge_base, sources_guide

    # ══════════════════════════════════════════════════════════════════════════
    # MAIN RUN — THREE-PHASE PIPELINE
    # ══════════════════════════════════════════════════════════════════════════

    async def run(self, start_query: str = "Start"):
        print(f"Planner {start_query} state before run:", self.state)
        self._pipeline_instructions = {}
        
        # Only run if not already confirmed
        if self.state.hypothesis.state != "confirmed":
            print("state not confirmed, planning phases engaged")

            # ─────────────────────────────────────────────────────────────
            # PHASE 1: HYPOTHESIS STRUCTURING
            # ─────────────────────────────────────────────────────────────
            await self._send("msg", "**Phase 1: Hypothesis Structuring**")
            
            hypothesis_architect = self.agents["hypothesis_architect"]

            # Get initial user input
            if start_query and not self.state.hypothesis.title:
                user_input = start_query
            else:
                await self._send("msg", "Please enter your clinical observation or research idea.")
                user_input = await self.input_provider()

            # Generate initial PICO hypothesis
            prompt = (
                f"User's clinical observation: {user_input}\n\n"
                "Structure this into a full PICO hypothesis. "
                "Return JSON with 'hypothesis' and 'message' fields."
            )

            response = await hypothesis_architect.on_messages(
                [TextMessage(content=prompt, source="user")],
                cancellation_token=None
            )
            print(f"response from hypothesis_architect: {response.chat_message.content}")
            await self._process_hypothesis_response(response.chat_message.content)

            # Hypothesis refinement loop
            while True:
                await self._send("msg", "Review the hypothesis. Type **APPROVE** to confirm or describe changes.")
                user_feedback = await self.input_provider()

                if "APPROVE" in user_feedback.upper():
                    break

                refinement_prompt = (
                    f"User Feedback: {user_feedback}\n\n"
                    "Update the hypothesis based on their feedback. "
                    "Return JSON with updated 'hypothesis' and 'message'. "
                    "Only change the fields the user mentions."
                )

                response = await hypothesis_architect.on_messages(
                    [TextMessage(content=refinement_prompt, source="user")],
                    cancellation_token=None
                )
                await self._process_hypothesis_response(response.chat_message.content)

            # Mark hypothesis confirmed
            self.state.hypothesis.state = "confirmed"
            await self._save_state()

            # ─────────────────────────────────────────────────────────────
            # PHASE 2: LITERATURE REVIEW & KNOWLEDGE BUILDING
            # ─────────────────────────────────────────────────────────────
            await self._send("msg", "**Phase 2: Literature Review & Knowledge Building**")
            await self._send("msg", "Building domain knowledge base from PubMed, MeSH, Wikipedia, Semantic Scholar, and Europe PMC...")

            try:
                knowledge_base, sources_guide = await self._build_knowledge_base()
                
                # Report knowledge base stats to user
                kb_size = len(knowledge_base)
                sg_size = len(sources_guide)
                await self._send("msg", (
                    f"Knowledge base built successfully.\n"
                    f"- Domain knowledge: {kb_size:,} characters\n"
                    f"- Sources guide: {sg_size:,} characters\n\n"
                    "Proceeding to pipeline planning informed by literature evidence..."
                ))
            except Exception as e:
                logger.error(f"Knowledge building failed: {e}", exc_info=True)
                await self._send("msg", (
                    f"Warning: Knowledge base construction failed ({e}). "
                    "Pipeline planning will proceed without literature augmentation."
                ))
                knowledge_base = ""
                sources_guide = ""

            # ─────────────────────────────────────────────────────────────
            # PHASE 3: DYNAMIC PIPELINE PLANNING
            # ─────────────────────────────────────────────────────────────
            await self._send("msg", "**Phase 3: Pipeline Planning**")
            
            pipeline_architect = self.agents["pipeline_architect"]

            # Build the hypothesis summary for the pipeline architect
            hyp = self.state.hypothesis
            hypothesis_summary = json.dumps(hyp.model_dump(), indent=2)
            
            # Truncate knowledge base for prompt (keep key sections)
            kb_context = knowledge_base[:6000] if knowledge_base else "No domain knowledge available."

            pipeline_prompt = (
                f"CONFIRMED HYPOTHESIS:\n{hypothesis_summary}\n\n"
                f"DOMAIN KNOWLEDGE BASE (from literature review):\n{kb_context}\n\n"
                "Based on this hypothesis and the domain knowledge, design an optimal "
                "research pipeline. Select the agents needed, order them logically, and "
                "provide detailed, study-specific instructions for each agent. "
                "Return JSON with 'steps', 'rationale', and 'message'."
            )

            response = await pipeline_architect.on_messages(
                [TextMessage(content=pipeline_prompt, source="user")],
                cancellation_token=None
            )
            print(f"response from pipeline_architect: {response.chat_message.content}")
            await self._process_pipeline_response(response.chat_message.content)

            # Pipeline refinement loop
            while True:
                await self._send("msg", "Review the pipeline plan. Type **APPROVE** to confirm or describe changes.")
                user_feedback = await self.input_provider()

                if "APPROVE" in user_feedback.upper():
                    break

                refinement_prompt = (
                    f"User Feedback: {user_feedback}\n\n"
                    "Update the pipeline plan based on their feedback. "
                    "You can add, remove, or reorder steps. Update instructions as needed. "
                    "Return JSON with updated 'steps', 'rationale', and 'message'."
                )

                response = await pipeline_architect.on_messages(
                    [TextMessage(content=refinement_prompt, source="user")],
                    cancellation_token=None
                )
                await self._process_pipeline_response(response.chat_message.content)

            # ─────────────────────────────────────────────────────────────
            # FINALIZE: Compile task messages with context augmentation
            # ─────────────────────────────────────────────────────────────
            self.state.plan.state = "confirmed"
            await self._save_state()

            await self._send("msg", "Pipeline confirmed. Compiling context-enriched task messages for each agent...")

            try:
                # Compile structured task messages with knowledge slices
                task_messages = compile_task_messages(
                    hypothesis=self.state.hypothesis,
                    plan=self.state.plan,
                    knowledge_base=knowledge_base,
                    sources_guide=sources_guide,
                    pipeline_instructions=self._pipeline_instructions,
                )
                self.state.task_messages = task_messages
                await self._save_state()

                await self._send("msg", f"Generated {len(task_messages)} context-enriched task messages.")
                
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
                        "instructions_preview": tm.instructions[:150] + "..." 
                            if len(tm.instructions) > 150 else tm.instructions,
                    }
                    for tm in task_messages
                ]
                await self._send("task_messages", task_summaries)
            except Exception as e:
                logger.error(f"Task message compilation failed (non-fatal): {e}", exc_info=True)
                await self._send("msg", f"Warning: Task message compilation failed ({e}). Pipeline will proceed with basic instructions.")

            await self._send("modules_matched", True)
