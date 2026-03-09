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
        model_client=None,
        api_key: str = None,
        session_id: str = "",
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
        
        if model_client is not None:
            self.client = model_client
        else:
            self.client = create_model_client(api_key or "")
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
        # Use greedy .* to capture the full nested JSON object
        fence_match = re.search(r"```json\s*(\{.*\})\s*```", text, re.DOTALL)
        if fence_match:
            return fence_match.group(1).strip()

        # Try plain ``` ... ``` code fence
        fence_match = re.search(r"```\s*(\{.*\})\s*```", text, re.DOTALL)
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

    @staticmethod
    def _fix_invalid_json_escapes(text: str) -> str:
        """Fix invalid backslash escapes that break json.loads().

        LLMs often embed LaTeX (\\documentclass, \\begin{table}) or Windows
        paths (\\Users) in JSON string values.  These are not valid JSON
        escape sequences and cause ``Invalid \\escape`` errors.

        Valid JSON escapes are: \\" \\\\ \\/ \\b \\f \\n \\r \\t \\uXXXX
        Any backslash NOT followed by one of those characters is doubled so
        json.loads() will accept it.
        """
        return re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text)

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
        
        # Try parsing — if invalid escapes, fix and retry
        data = None
        for attempt, content in enumerate(
            [clean_content, self._fix_invalid_json_escapes(clean_content)]
        ):
            try:
                data = json.loads(content)
                break
            except json.JSONDecodeError:
                if attempt == 0:
                    continue

        if data is None:
            display_text = response_content.replace("APPROVE", "").strip()
            if display_text:
                await self._send("msg", display_text)
            return

        if "hypothesis" in data and isinstance(data["hypothesis"], dict):
            hypo_data = data["hypothesis"]
            
            if "id" not in hypo_data or not hypo_data["id"]: 
                hypo_data["id"] = self.state.hypothesis.id or str(uuid.uuid4())
            
            self.state.hypothesis = ClinicalHypothesis(**hypo_data)
            await self._send("hypothesis", self.state.hypothesis.model_dump())

        if "message" in data and data["message"]:
            await self._send("msg", data["message"])
        
        await self._save_state()

    async def _process_pipeline_response(self, response_content: str):
        """Parse pipeline plan JSON from the PipelineArchitect and update state/UI."""
        logger.info(f"[PipelineArchitect]: {response_content[:100]}...")
        
        clean_content = self._clean_json(response_content)
        
        # Try parsing — if invalid escapes (LaTeX, paths), fix and retry
        data = None
        for attempt, content in enumerate(
            [clean_content, self._fix_invalid_json_escapes(clean_content)]
        ):
            try:
                data = json.loads(content)
                break
            except json.JSONDecodeError as e:
                if attempt == 0:
                    logger.warning(f"Pipeline JSON attempt 1 failed ({e}), retrying with escape fix...")
                    continue
                logger.error(f"Pipeline JSON parse error after escape fix: {e}")
                logger.error(f"Clean content (first 300 chars): {clean_content[:300]}")
                print(f"[DEBUG] Pipeline JSON parse error: {e}")
                print(f"[DEBUG] Clean content preview: {clean_content[:200]}...")

        if data is None:
            display_text = response_content.replace("APPROVE", "").strip()
            if display_text:
                await self._send("msg", display_text)
            return

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

        # Rationale is included in the plan payload for the canvas, not sent to chat

        if "message" in data and data["message"]:
            await self._send("msg", data["message"])
        
        await self._save_state()

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
        Returns (knowledge_base, sources_guide, papers) tuple.
        Sends short progress logs to the frontend via WebSocket.
        """
        pico = self._extract_pico_from_hypothesis()
        logger.info(f"Building knowledge base with PICO: {pico}")
        
        loop = asyncio.get_event_loop()

        def progress_callback(message: str):
            """Thread-safe bridge: schedule WS send on the event loop."""
            asyncio.run_coroutine_threadsafe(
                self._send("lit_review_log", {"message": message, "status": "active"}),
                loop
            )

        knowledge_base, sources_guide, papers = await loop.run_in_executor(
            None, lambda: build_knowledge_base(pico, progress_callback=progress_callback)
        )
        
        logger.info(
            f"Knowledge base built: {len(knowledge_base):,} chars, "
            f"sources guide: {len(sources_guide):,} chars, "
            f"papers collected: {len(papers)}"
        )
        
        self.state.knowledge_base = knowledge_base
        self.state.sources_guide = sources_guide
        await self._save_state()
        
        return knowledge_base, sources_guide, papers

    async def _save_literature_context(self, knowledge_base: str, sources_guide: str):
        """
        Save the literature review context (knowledge base + sources guide)
        to the workspace as a structured JSON file that the reporter agent
        can read via the read_literature_context MCP tool.
        """
        workspace_dir = self.state_path.parent
        lit_context_path = workspace_dir / "literature_context.json"
        
        context_data = {
            "knowledge_base": knowledge_base,
            "sources_guide": sources_guide,
            "hypothesis": self.state.hypothesis.model_dump() if self.state.hypothesis else {},
        }
        
        try:
            async with aiofiles.open(lit_context_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(context_data, indent=2, ensure_ascii=False))
            logger.info(f"Literature context saved to {lit_context_path}")
        except Exception as e:
            logger.error(f"Failed to save literature context: {e}")

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

            # Hypothesis refinement loop — ask client via WebSocket to approve
            while True:
                await self._send("hypothesis_approval", {
                    "message": "Review the structured PICO hypothesis. Approve or request changes."
                })
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
            await self._send("hypothesis", self.state.hypothesis.model_dump())
            await self._save_state()

            # ─────────────────────────────────────────────────────────────
            # PHASE 2: LITERATURE REVIEW & KNOWLEDGE BUILDING (user choice)
            # ─────────────────────────────────────────────────────────────
            await self._send("lit_review_choice", {
                "message": "Would you like to build a domain knowledge base?"
            })
            lit_review_answer = await self.input_provider()
            do_lit_review = "YES" in lit_review_answer.upper()

            knowledge_base = ""
            sources_guide = ""

            if do_lit_review:
                # Create the literature review agent node on the canvas
                await self._send("lit_review_node", True)
                await self._send("lit_review_log", {"message": "Starting literature review", "status": "active"})

                try:
                    knowledge_base, sources_guide, papers = await self._build_knowledge_base()
                    
                    # Save literature context to workspace for the reporter agent
                    await self._save_literature_context(knowledge_base, sources_guide)

                    # Try to read a pre-existing literature report if available
                    report_md = ""
                    report_path = Path(__file__).parent.parent / "literature_review_agent" / "literature_review_output" / "literature_report.md"
                    if report_path.exists():
                        try:
                            report_md = report_path.read_text(encoding="utf-8")
                        except Exception:
                            pass

                    # Send the full literature output to the frontend for visualization
                    lit_payload: dict = {
                        "knowledge_base": knowledge_base,
                        "sources_guide": sources_guide,
                        "hypothesis": self.state.hypothesis.model_dump() if self.state.hypothesis else {},
                        "papers": papers,
                    }
                    if report_md:
                        lit_payload["report"] = report_md
                    await self._send("lit_review_output", lit_payload)

                    await self._send("lit_review_done", True)
                    
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
                    await self._send("lit_review_done", True)
                    await self._send("msg", (
                        f"Warning: Knowledge base construction failed ({e}). "
                        "Pipeline planning will proceed without literature augmentation."
                    ))
            else:
                await self._send("msg", "Skipping literature review. Proceeding to pipeline planning...")

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
